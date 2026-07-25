"""Onboarding self-service — provisionamento automático de empresa (tenant).

Fluxo (ver docs/AUDITORIA_SEPARACAO_REPOS.md e a épica de onboarding):

  1. O cliente se cadastra numa página pública informando os dados da empresa e
     do responsável. Isso cria a empresa em estado PENDENTE (``active = 0``,
     ``license_status = 'pending'``) e o usuário dono (``general_admin``)
     inativo — ainda sem senha utilizável.
  2. O cliente é levado ao checkout do Mercado Pago (fluxo já existente),
     usando o ``company_id`` recém-criado.
  3. Quando o pagamento é aprovado, o webhook do Mercado Pago chama
     ``activate_tenant_and_notify``: a empresa é ativada, a senha do dono é
     gerada e enviada por e-mail.

Toda a lógica sensível roda no backend; nenhuma senha em texto puro é
persistida nem devolvida ao frontend — apenas enviada por e-mail ao dono.
"""

import re
import secrets

from core.security import hash_password
from epi_backend.http_utils import structured_log
from modules.companies.service import create_company, validate_company_payload

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Estado da empresa enquanto o pagamento não é confirmado.
PENDING_LICENSE_STATUS = 'pending'
OWNER_ROLE = 'general_admin'


def _clean(value):
    return str(value or '').strip()


def _validate_email(value):
    email = _clean(value).lower()
    if not _EMAIL_RE.match(email):
        raise ValueError('E-mail do responsável inválido.')
    return email


def _username_taken(connection, username):
    row = connection.execute(
        'SELECT id FROM users WHERE LOWER(username) = ?', (username.lower(),)
    ).fetchone()
    return row is not None


def provision_pending_tenant(connection, payload):
    """Cria uma empresa PENDENTE + o usuário dono (general_admin) inativo.

    Não exige ator autenticado — é o fluxo público de cadastro. Reaproveita toda
    a validação comercial de ``validate_company_payload`` (CNPJ real, plano,
    limite de usuários, unicidade de CNPJ). A empresa nasce inativa; o dono só
    ganha senha e acesso quando o pagamento é confirmado (``activate...``).

    Retorna um dict com ``company_id``/``owner_user_id`` para o frontend seguir
    ao checkout.
    """
    payload = payload or {}
    owner_name = _clean(payload.get('owner_name'))
    owner_email = _validate_email(payload.get('owner_email'))
    if not owner_name:
        raise ValueError('Nome do responsável é obrigatório.')

    # O login do dono é o próprio e-mail; o login é global, então precisa ser
    # único em toda a base (não só na empresa).
    if _username_taken(connection, owner_email):
        raise ValueError('Já existe um usuário cadastrado com este e-mail.')

    company_payload = {
        'name': payload.get('name'),
        'legal_name': payload.get('legal_name'),
        'cnpj': payload.get('cnpj'),
        'plan_name': payload.get('plan_name') or 'start',
        'user_limit': payload.get('user_limit'),
        'license_status': PENDING_LICENSE_STATUS,
        'contact_email': owner_email,
    }
    validated = validate_company_payload(connection, company_payload, None)
    # validate_company_payload normaliza license_status para o default 'active';
    # reforçamos o estado pendente e a inatividade.
    validated['license_status'] = PENDING_LICENSE_STATUS
    validated['active'] = 0
    company_id = create_company(connection, validated)

    # Estrutura organizacional escolhida no onboarding (single_cnpj, multi_cnpj,
    # holding, group, joint_venture, consortium, other). Persistida na tenant
    # para orientar o fluxo de cadastro de CNPJs (único vs. lote vs. JV).
    if payload.get('org_structure_type'):
        from core.schema import _table_columns
        from modules.legal_entities.service import normalize_org_structure_type
        if 'org_structure_type' in _table_columns(connection, 'companies'):
            connection.execute(
                'UPDATE companies SET org_structure_type = ? WHERE id = ?',
                (normalize_org_structure_type(payload.get('org_structure_type')), int(company_id)),
            )

    # Senha placeholder aleatória e inutilizável — a real é gerada na ativação.
    placeholder = hash_password(secrets.token_urlsafe(24))
    cursor = connection.execute(
        'INSERT INTO users (username, password, full_name, role, company_id, active, linked_employee_id) '
        'VALUES (?, ?, ?, ?, ?, 0, NULL)',
        (owner_email, placeholder, owner_name, OWNER_ROLE, company_id),
    )
    owner_user_id = int(cursor.lastrowid)

    structured_log('info', 'onboarding.tenant_provisioned',
                   company_id=company_id, owner_user_id=owner_user_id,
                   plan_name=validated['plan_name'])
    return {
        'company_id': int(company_id),
        'owner_user_id': owner_user_id,
        'owner_email': owner_email,
        'plan_name': validated['plan_name'],
        'user_limit': validated['user_limit'],
        'status': 'pending',
    }


def activate_tenant_and_notify(connection, company_id, *, force_resend=False):
    """Ativa a empresa e envia as credenciais do dono por e-mail. Idempotente.

    Chamada pelo webhook quando o pagamento é aprovado (e por um endpoint de
    suporte do master). Se a empresa já estiver ativa, não faz nada — evita
    reenviar o e-mail a cada notificação repetida do Mercado Pago — a menos que
    ``force_resend=True`` (reemissão manual de credenciais).

    O envio de e-mail é best-effort: uma falha de SMTP não impede a ativação da
    empresa (fica registrada em log para reenvio manual).
    """
    company_id = int(company_id)
    row = connection.execute(
        'SELECT id, name, active FROM companies WHERE id = ?', (company_id,)
    ).fetchone()
    if not row:
        raise ValueError('Empresa não encontrada.')
    company = dict(row)
    already_active = int(company.get('active') or 0) == 1
    if already_active and not force_resend:
        return {'company_id': company_id, 'activated': False, 'email_sent': False,
                'reason': 'already_active'}

    connection.execute(
        "UPDATE companies SET active = 1, license_status = 'active' WHERE id = ?",
        (company_id,),
    )

    owner = connection.execute(
        'SELECT id, username, full_name FROM users '
        'WHERE company_id = ? AND role = ? ORDER BY id ASC LIMIT 1',
        (company_id, OWNER_ROLE),
    ).fetchone()
    if not owner:
        structured_log('warning', 'onboarding.activate_no_owner', company_id=company_id)
        return {'company_id': company_id, 'activated': True, 'email_sent': False,
                'reason': 'no_owner'}
    owner = dict(owner)

    password = secrets.token_urlsafe(9)  # ~12 caracteres
    connection.execute(
        'UPDATE users SET password = ?, active = 1 WHERE id = ?',
        (hash_password(password), owner['id']),
    )

    email_sent = False
    try:
        send_credentials_email(owner['username'], password, company.get('name') or '')
        email_sent = True
    except Exception as exc:  # best-effort — não bloqueia a ativação
        structured_log('warning', 'onboarding.credentials_email_failed',
                       company_id=company_id, error=str(exc))

    structured_log('info', 'onboarding.tenant_activated',
                   company_id=company_id, owner_user_id=owner['id'], email_sent=email_sent)
    return {'company_id': company_id, 'activated': True, 'email_sent': email_sent,
            'owner_username': owner['username']}


def send_credentials_email(to_email, password, company_name):
    """Envia as credenciais de acesso do dono via SMTP (stdlib ``smtplib``).

    Reaproveita a mesma infraestrutura de SMTP do reset de senha
    (variáveis ``SMTP_*``). Levanta ``ValueError`` se o SMTP não estiver
    configurado — tratado como best-effort por quem chama.
    """
    import os
    import smtplib
    from email.mime.text import MIMEText

    from epi_backend.config import (
        SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER,
    )

    if not SMTP_HOST or not SMTP_USER:
        raise ValueError('Servidor de e-mail não configurado (SMTP_HOST/SMTP_USER).')

    login_url = (
        os.environ.get('WEB_APP_URL', '').strip()
        or os.environ.get('PUBLIC_BASE_URL', '').strip()
    ).rstrip('/')
    access_line = f'Acesse: {login_url}\n\n' if login_url else ''
    body = (
        f'Olá,\n\n'
        f'A empresa "{company_name}" foi criada no EPI Controle e o seu acesso de '
        f'administrador está pronto.\n\n'
        f'{access_line}'
        f'  Login: {to_email}\n'
        f'  Senha provisória: {password}\n\n'
        f'Por segurança, altere a senha após o primeiro acesso.\n'
        f'Depois de entrar, você pode configurar os dados, a logo e a página de '
        f'login da sua empresa.\n\n'
        f'Atenciosamente,\nEPI Controle'
    )
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = 'Bem-vindo ao EPI Controle — acesso da sua empresa'
    from_addr = SMTP_FROM or SMTP_USER
    msg['From'] = from_addr
    msg['To'] = to_email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.sendmail(from_addr, [to_email], msg.as_string())

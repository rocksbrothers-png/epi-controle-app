/*
 * Cadastro self-service servido pelo backend (mesma origem da API
 * /api/onboarding/*), no padrão do pagamento.js.
 *
 *  - O site institucional redireciona para esta página passando
 *    ?plan=<chave>&cycle=<ciclo>&lang=<idioma>.
 *  - O formulário provisiona a empresa (estado pendente) via
 *    POST /api/onboarding/signup e, com o company_id devolvido, segue para
 *    o checkout (/pagamento) já existente — que ativa a empresa quando o
 *    pagamento é aprovado.
 */

const API = {
  signup: '/api/onboarding/signup',
  catalog: '/api/payments/catalog',
};

// Mesmos mínimos de usuários da configuração comercial padrão do backend
// (modules/commercial/service.py) — usados apenas como dica no formulário;
// a validação real (e o valor por plano configurado) é feita no backend.
const PLAN_MIN_USERS = { start: 1, business: 11, corporate: 26 };

const I18N = {
  pt: {
    submitting: 'Enviando…',
    submit: 'Continuar para pagamento',
    users_hint_min: 'O plano exige no mínimo {n} usuário(s).',
  },
  en: {
    submitting: 'Submitting…',
    submit: 'Continue to payment',
    users_hint_min: 'This plan requires at least {n} user(s).',
  },
};

const params = new URLSearchParams(window.location.search);
const ctx = {
  plan: (params.get('plan') || 'start').trim().toLowerCase(),
  cycle: (params.get('cycle') || 'monthly').trim().toLowerCase() === 'annual' ? 'annual' : 'monthly',
  lang: (params.get('lang') || 'pt').trim().toLowerCase().slice(0, 2),
};
if (!PLAN_MIN_USERS[ctx.plan]) ctx.plan = 'start';
const t = (key) => (I18N[ctx.lang] || I18N.pt)[key] || I18N.pt[key] || key;

const $ = (id) => document.getElementById(id);

function showError(message) {
  const box = $('error-box');
  box.textContent = message;
  box.hidden = !message;
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error((data.error && data.error.message) || `Erro HTTP ${res.status}`);
  }
  return data;
}

async function getJson(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  return res.json().catch(() => ({}));
}

function updateUserLimitHint() {
  const min = PLAN_MIN_USERS[$('plan_name').value] || 1;
  $('user_limit').min = String(min);
  if (!$('user_limit').value || Number($('user_limit').value) < min) {
    $('user_limit').value = String(min);
  }
  $('user-limit-hint').textContent = t('users_hint_min').replace('{n}', min);
}

function goToCheckout(companyId) {
  const search = new URLSearchParams({
    plan: ctx.plan, cycle: ctx.cycle, lang: ctx.lang, company_id: String(companyId),
  });
  window.location.href = `/pagamento?${search.toString()}`;
}

async function onSubmit(event) {
  event.preventDefault();
  showError('');
  const submitBtn = $('submitBtn');
  submitBtn.disabled = true;
  submitBtn.textContent = t('submitting');
  try {
    const payload = {
      name: $('name').value.trim(),
      legal_name: $('legal_name').value.trim(),
      cnpj: $('cnpj').value.trim(),
      plan_name: $('plan_name').value,
      user_limit: Number($('user_limit').value || 0),
      owner_name: $('owner_name').value.trim(),
      owner_email: $('owner_email').value.trim(),
    };
    const data = await postJson(API.signup, payload);
    goToCheckout(data.company.company_id);
  } catch (err) {
    showError(String(err.message || err));
    submitBtn.disabled = false;
    submitBtn.textContent = t('submit');
  }
}

async function init() {
  $('plan_name').value = ctx.plan;
  updateUserLimitHint();
  $('plan_name').addEventListener('change', updateUserLimitHint);
  $('signup-form').addEventListener('submit', onSubmit);

  try {
    const resp = await getJson(`${API.catalog}?cycle=${ctx.cycle}`);
    const item = (resp.catalog || []).find((p) => (p.key || '').toLowerCase() === ctx.plan);
    if (item) $('plan-name').textContent = item.label || ctx.plan;
    else $('plan-name').textContent = ctx.plan.toUpperCase();
  } catch (_e) {
    $('plan-name').textContent = ctx.plan.toUpperCase();
  }
}

init();

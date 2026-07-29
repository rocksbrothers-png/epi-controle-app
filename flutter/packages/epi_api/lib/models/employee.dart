class Employee {
  const Employee({
    required this.id,
    required this.name,
    this.code,
    this.sector,
    this.role,
    this.unitId,
    this.unitName,
    this.admissionDate,
    this.schedule,
    this.photoUrl,
    this.isActive = true,
    this.legalEntityId,
    this.legalEntityCnpj,
    this.legalEntityName,
    this.employmentType,
    this.sourceCompany,
  });

  final int id;
  final String name;
  final String? code;
  final String? sector;
  final String? role;

  /// Unidade operacional atual (`current_unit_id`) — reflete movimentação
  /// temporária ativa quando houver; senão, a unidade-base do cadastro.
  final int? unitId;
  final String? unitName;
  final String? admissionDate;
  final String? schedule;
  final String? photoUrl;
  final bool isActive;

  /// CNPJ (LegalEntity) ao qual o colaborador pertence juridicamente.
  ///
  /// É o vínculo do contrato de trabalho: **imutável após a admissão**. A
  /// unidade é apenas a lotação operacional e pode mudar por transferência sem
  /// afetar este vínculo. Alterá-lo exige o processo administrativo auditado
  /// (`LegalEntitiesApi.transferEmployeeLegalEntity`).
  final int? legalEntityId;
  final String? legalEntityCnpj;
  final String? legalEntityName;

  /// Tipo de vínculo (`tipo_vinculo`): `CLT`, `Terceirizado`, `Temporário`,
  /// `Prestador de Serviço`, `Menor Aprendiz`, `Praticante` ou `Estagiário`.
  ///
  /// Texto livre no backend, sem lista fechada validada no servidor — os
  /// valores acima são os que a UI oferece, espelhando o web legado.
  final String? employmentType;

  /// Empresa de origem (`empresa_origem`), preenchida só quando
  /// [employmentType] é diferente de `CLT`. O backend zera este campo
  /// automaticamente quando o vínculo volta a ser CLT.
  final String? sourceCompany;

  factory Employee.fromJson(Map<String, dynamic> json) => Employee(
        id: (json['id'] as num).toInt(),
        name: json['name'] as String? ?? '',
        // O backend (fetch_employees/bootstrap) usa employee_id_code,
        // role_name e schedule_type — os nomes curtos ficam como fallback.
        code: json['employee_id_code'] as String? ?? json['code'] as String?,
        sector: json['sector'] as String?,
        role: json['role_name'] as String? ?? json['role'] as String?,
        unitId: (json['current_unit_id'] as num?)?.toInt() ??
            (json['unit_id'] as num?)?.toInt(),
        unitName: json['current_unit_name'] as String? ??
            json['unit_name'] as String? ??
            json['unit'] as String?,
        admissionDate: json['admission_date'] as String?,
        schedule: json['schedule_type'] as String? ?? json['schedule'] as String?,
        photoUrl: json['photo_url'] as String?,
        // O backend não envia is_active/active para colaboradores (não há
        // essa coluna); aceita bool ou 0/1 e assume ativo quando ausente.
        isActive: switch (json['is_active'] ?? json['active']) {
          final bool b => b,
          final num n => n.toInt() == 1,
          _ => true,
        },
        // Ausentes enquanto o schema Multi-CNPJ não estiver provisionado.
        legalEntityId: (json['legal_entity_id'] as num?)?.toInt(),
        legalEntityCnpj: json['legal_entity_cnpj'] as String?,
        legalEntityName: json['legal_entity_name'] as String?,
        employmentType: json['tipo_vinculo'] as String?,
        sourceCompany: json['empresa_origem'] as String?,
      );
}

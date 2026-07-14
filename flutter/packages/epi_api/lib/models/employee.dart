class Employee {
  const Employee({
    required this.id,
    required this.name,
    this.code,
    this.sector,
    this.role,
    this.unitName,
    this.admissionDate,
    this.schedule,
    this.photoUrl,
    this.isActive = true,
  });

  final int id;
  final String name;
  final String? code;
  final String? sector;
  final String? role;
  final String? unitName;
  final String? admissionDate;
  final String? schedule;
  final String? photoUrl;
  final bool isActive;

  factory Employee.fromJson(Map<String, dynamic> json) => Employee(
        id: (json['id'] as num).toInt(),
        name: json['name'] as String? ?? '',
        // O backend (fetch_employees/bootstrap) usa employee_id_code,
        // role_name e schedule_type — os nomes curtos ficam como fallback.
        code: json['employee_id_code'] as String? ?? json['code'] as String?,
        sector: json['sector'] as String?,
        role: json['role_name'] as String? ?? json['role'] as String?,
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
      );
}

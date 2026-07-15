import 'package:dio/dio.dart';
import '../models/ficha_config.dart';

class SettingsApi {
  const SettingsApi(this._dio);
  final Dio _dio;

  /// [companyId] é obrigatório para o master_admin (que não tem empresa
  /// própria) e ignorado para admins de empresa (o backend força a própria).
  Future<FichaConfig> getFichaConfig({int? companyId}) async {
    final res = await _dio.get<Map<String, dynamic>>(
      '/api/ficha-config',
      queryParameters: companyId != null ? {'company_id': companyId} : null,
    );
    return FichaConfig.fromJson(res.data ?? {});
  }

  Future<void> updateFichaConfig(FichaConfig config, {int? companyId}) async {
    await _dio.post<void>(
      '/api/ficha-config',
      data: {
        ...config.toJson(),
        if (companyId != null) 'company_id': companyId,
      },
    );
  }
}

# Fixa o deployment target mínimo do iOS no Podfile E no Runner.xcodeproj
# gerados pelo Flutter.
#
# Nem o Podfile nem o Runner.xcodeproj são versionados (gerados do zero por
# `flutter create`/`flutter build ios --config-only`, ver ios_ci.yml /
# deploy-ios.yml). Plugins como google_mlkit_commons passaram a exigir
# iOS >= 15.5 via CocoaPods; sem uma linha `platform :ios` explícita, o
# CocoaPods assume 13.0 e a resolução falha:
#
#   [!] CocoaPods could not find compatible versions for pod
#   "google_mlkit_commons": ... required a higher minimum deployment target.
#   Error: ... increase your application's deployment target to at least 15.5
#
# Separadamente, o Firebase (firebase_core/firebase_messaging) passou a ser
# integrado via Swift Package Manager, não CocoaPods — e a resolução de
# pacotes SPM do Xcode olha o IPHONEOS_DEPLOYMENT_TARGET do PRÓPRIO target
# Runner no project.pbxproj (13.0 por padrão, herdado do `flutter create`),
# não o `platform :ios` do Podfile. Sem também corrigir o pbxproj, o build
# falha com:
#
#   Target Integrity (Xcode): The package product 'firebase-core' requires
#   minimum platform version 15.0 for the iOS platform, but this target
#   supports 13.0
#
# Por isso este script cobre os dois lados com o mesmo valor mínimo.
#
# No Podfile, só declaramos `platform :ios, 'X'` — não adicionamos um
# post_install próprio: o CocoaPods 1.17 rejeita blocos post_install
# duplicados ("Specifying multiple `post_install` hooks is unsupported"), e
# o Podfile gerado pelo Flutter já vem com o seu próprio post_install
# padrão. Declarar `platform :ios` já é suficiente para a resolução de
# dependências (é exatamente o valor que o CocoaPods compara contra o
# deployment target mínimo exigido pelos pods) e o CocoaPods já aplica esse
# mínimo como IPHONEOS_DEPLOYMENT_TARGET para todos os pod targets por
# padrão.
#
# Idempotente nos dois lados: pode rodar mais de uma vez sem duplicar a
# linha `platform` nem sem efeito colateral ao regravar o mesmo valor de
# build setting.
#
# Uso: `ruby ios/fastlane/pin_ios_deployment_target.rb` a partir de
# flutter/apps/epi_admin, DEPOIS que o Podfile E o Runner.xcodeproj já
# existem (ex.: após `flutter build ios --config-only` e `wire_xcode.rb`).

require "xcodeproj"

IOS_DEPLOYMENT_TARGET = "15.5"

ios_dir = File.expand_path("..", __dir__) # .../ios
podfile_path = File.join(ios_dir, "Podfile")
proj_path = File.join(ios_dir, "Runner.xcodeproj")

unless File.exist?(podfile_path)
  abort("[pin_ios_deployment_target] #{podfile_path} não existe. Rode " \
        "`flutter build ios --config-only` antes.")
end

unless File.exist?(proj_path)
  abort("[pin_ios_deployment_target] #{proj_path} não existe. Rode " \
        "`flutter create . --platforms=ios` antes.")
end

# ── Podfile (CocoaPods) ──────────────────────────────────────────────────
content = File.read(podfile_path)

# Remove qualquer linha `platform :ios` pré-existente (comentada ou não) —
# vamos declarar a nossa, explícita, no topo.
lines = content.lines.reject { |l| l =~ /^\s*#?\s*platform\s+:ios\b/ }
content = lines.join

content = "platform :ios, '#{IOS_DEPLOYMENT_TARGET}'\n" + content

File.write(podfile_path, content)
puts "[pin_ios_deployment_target] platform :ios, '#{IOS_DEPLOYMENT_TARGET}' aplicado em #{podfile_path}"

# ── Runner.xcodeproj (Xcode / Swift Package Manager) ────────────────────
project = Xcodeproj::Project.open(proj_path)

project.build_configurations.each do |config|
  config.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] = IOS_DEPLOYMENT_TARGET
end

project.targets.each do |target|
  target.build_configurations.each do |config|
    config.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] = IOS_DEPLOYMENT_TARGET
  end
end

project.save
puts "[pin_ios_deployment_target] IPHONEOS_DEPLOYMENT_TARGET = #{IOS_DEPLOYMENT_TARGET} aplicado em #{proj_path} (projeto + #{project.targets.size} target(s))"

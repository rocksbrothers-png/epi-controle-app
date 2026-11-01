"""FIX-SAAS-PYMUPDF-BUILD-V1 — a Biblioteca de Segurança fica no PyMuPDF 1.x até validar a 2.x.

O PyMuPDF 2.0 (bump automático do Dependabot) passou a importar `onnxruntime` e a executar
um modelo de layout já no `import pymupdf`. O worker isolado da Biblioteca
(`modules/safety_library/render_worker.py`) importa o PyMuPDF a cada pré-visualização, dentro
do sandbox (seccomp + RLIMIT_CPU 80 s + RLIMIT_AS 3 GiB): o import passou de ~0,1 s/90 MB para
~0,7 s/~1 GB de memória virtual e uma thread por CPU visível, muito mais lento sob cota de CPU.
O `check_library_runtime.py` do build Docker no Render estourou o limite de 100 s e os deploys
de 26/10 falharam. Ver docs/FIX_SAAS_PYMUPDF_BUILD_V1.md.

Estes testes travam:
- a faixa do `requirements.txt` (exclui a 2.x; aceita a 1.28.2 validada);
- o ignore temporário do Dependabot, só para a 2.x, preservando os ignores existentes;
- no ambiente instalado (CI), que o PyMuPDF é 1.x e que importá-lo não carrega `onnxruntime`.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _pymupdf_requirement():
    for raw in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if line and re.match(r'(?i)pymupdf\b', line):
            return Requirement(line)
    raise AssertionError('PyMuPDF não declarado em requirements.txt')


def test_requirements_mantem_pymupdf_na_serie_1x():
    requirement = _pymupdf_requirement()
    assert Version('1.28.2') in requirement.specifier, 'a versão validada no build Docker precisa caber na faixa'
    for blocked in ('2.0', '2.0.1', '2.1', '3.0'):
        assert Version(blocked) not in requirement.specifier, f'PyMuPDF {blocked} não pode ser instalado'


def test_dependabot_ignora_so_a_serie_2x_do_pymupdf():
    text = (ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')
    pip_block = text.split('package-ecosystem: pip', 1)[1].split('package-ecosystem:', 1)[0]
    ignore = re.search(r'- dependency-name: "pymupdf"\n\s+versions: \["(.+?)"\]', pip_block)
    assert ignore, 'ignore do pymupdf ausente no bloco pip do Dependabot'
    assert ignore.group(1) == '>=2.0', 'o ignore deve cobrir só a 2.x (a série 1.x continua atualizando)'
    assert 'update-types' not in pip_block.split('- dependency-name: "pymupdf"', 1)[1].split('- dependency-name', 1)[0]
    assert '- dependency-name: "numpy"' in pip_block, 'o ignore existente do numpy deve ser preservado'


def test_pymupdf_instalado_nao_carrega_onnxruntime_no_import():
    pytest.importorskip('pymupdf')
    probe = (
        'import sys, pymupdf;'
        'print(pymupdf.__version__);'
        'print(any(m == "onnxruntime" or m.startswith("onnxruntime.") for m in sys.modules))'
    )
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=120, check=True)
    version, onnx_loaded = result.stdout.split()
    assert Version(version).major == 1, f'PyMuPDF {version} instalado; a Biblioteca exige a série 1.x'
    assert onnx_loaded == 'False', 'importar o PyMuPDF não pode carregar onnxruntime no worker da Biblioteca'

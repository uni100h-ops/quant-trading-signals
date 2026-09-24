"""Bootstrap with no external dependencies. Never receives, prints, or stores keys."""
from pathlib import Path
import hashlib,json,subprocess,sys

ROOT=Path(__file__).resolve().parent

def main():
    if sys.version_info[:2]!=(3,12):
        print('Python 3.12 is required.');return 1
    if sys.prefix==sys.base_prefix:
        print('Use INSTALAR.bat: dependencies are installed in .venv.');return 1
    lock=ROOT/'requirements.lock'
    digest=hashlib.sha256(lock.read_bytes()).hexdigest()
    marker=Path(sys.prefix)/'qts_instalacion.json'
    if marker.exists() and json.loads(marker.read_text()).get('lock_sha256')==digest:
        print('Dependencies for this version are already installed and verified.');return 0
    command=[sys.executable,'-m','pip','install','--disable-pip-version-check','--only-binary=:all:','--require-hashes','-r',str(lock)]
    print('Downloading and installing SHA-256-verified dependencies. Internet access is required. No keys are requested during this step.')
    if subprocess.run(command,cwd=ROOT).returncode:return 1
    if subprocess.run([sys.executable,'-m','pip','check'],cwd=ROOT).returncode:return 1
    if subprocess.run([sys.executable,str(ROOT/'agente.py'),'--help'],cwd=ROOT,stdout=subprocess.DEVNULL).returncode:return 1
    marker.write_text(json.dumps({'lock_sha256':digest,'python':sys.version.split()[0]})+'\n')
    print('Dependencies installed successfully.');return 0

if __name__=='__main__':raise SystemExit(main())

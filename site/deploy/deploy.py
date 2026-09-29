"""Publica o site na HostGator por FTP com TLS.

    python site/deploy/deploy.py --dry-run    # só lista o que seria enviado
    python site/deploy/deploy.py              # envia

Antes de tudo confere o git: com alteração não commitada ou commit não enviado
ao GitHub, para e diz o que falta — o que está no ar tem de estar no repositório.

Envia para a raiz da conta FTP (que já é o public_html):
- o conteúdo de site/, menos o que está em IGNORAR;
- de ../publicado/, só o que o site lê (ver js/dados.js), em publicado/.
  Os dados não estão no git: são reproduzíveis pelo pipeline.

Só vai o que é novo ou mudou: arquivo ausente no servidor, de tamanho diferente,
ou modificado aqui depois da última cópia de lá. O script não apaga nada no
servidor, e nunca envia nada para .well-known/ nem cgi-bin/.

Credenciais em site/.env (modelo em site/.env.example): FTP_HOST, FTP_USER, FTP_PASS.
Só biblioteca padrão.
"""

from __future__ import annotations

import argparse
import ftplib
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
PROJETO = SITE.parent
PUBLICADO = PROJETO / "publicado"

IGNORAR = {
    "deploy", "referencia", "dev", "CLAUDE.md", ".mcp.json", ".git", ".gitignore",
    ".env", ".env.example", "__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini",
}
PROTEGIDOS = (".well-known", "cgi-bin")

PUBLICADO_ARQUIVOS = ["ufs.json", "metadados.json"]
PUBLICADO_PASTAS = ["municipios", "ruas", "regioes", "bairros", "agregados", "compartilhar"]


def parar(msg: str) -> None:
    print(f"\nParado: {msg}")
    sys.exit(1)


# ---------------------------------------------------------------- git
def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=PROJETO, capture_output=True, text=True)


def conferir_git() -> None:
    status = git("status", "--porcelain")
    if status.returncode != 0:
        parar(f"não consegui rodar o git: {status.stderr.strip()}")
    if status.stdout.strip():
        parar("há alterações não commitadas:\n" + status.stdout.rstrip() +
              "\n\nFaça o commit (e o push) antes de publicar.")

    upstream = git("rev-parse", "--abbrev-ref", "@{u}")
    if upstream.returncode != 0:
        parar("o branch atual não tem um branch remoto associado. Rode: git push -u origin <branch>")
    pendentes = git("rev-list", "--count", "@{u}..HEAD")
    n = int(pendentes.stdout.strip() or 0)
    if n:
        parar(f"há {n} commit(s) não enviado(s) ao GitHub ({upstream.stdout.strip()}). Rode: git push")
    print(f"git ok: tudo commitado e enviado a {upstream.stdout.strip()}")


# ---------------------------------------------------------------- arquivos locais
def ignorado(nome: str) -> bool:
    return (nome in IGNORAR or nome.lower().startswith("readme")
            or nome.endswith((".pyc", "~")))


def arquivos_locais() -> dict[str, Path]:
    """Caminho no servidor -> arquivo local."""
    locais: dict[str, Path] = {}

    def varrer(pasta: Path, prefixo: str) -> None:
        for item in sorted(pasta.iterdir()):
            if ignorado(item.name):
                continue
            destino = f"{prefixo}{item.name}"
            if item.is_dir():
                varrer(item, destino + "/")
            else:
                locais[destino] = item

    varrer(SITE, "")
    for nome in PUBLICADO_ARQUIVOS:
        locais[f"publicado/{nome}"] = PUBLICADO / nome
    for nome in PUBLICADO_PASTAS:
        varrer(PUBLICADO / nome, f"publicado/{nome}/")

    return {k: v for k, v in locais.items() if not k.startswith(PROTEGIDOS)}


# ---------------------------------------------------------------- FTP
class FTPS(ftplib.FTP_TLS):
    """FTP_TLS que reaproveita a sessão TLS na conexão de dados — alguns
    servidores recusam a transferência sem isso (erro 522/425)."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host,
                                            session=self.sock.session)
        return conn, size


def ler_env() -> dict[str, str]:
    arquivo = SITE / ".env"
    if not arquivo.exists():
        parar(f"falta {arquivo}. Copie site/.env.example para site/.env e preencha.")
    env = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            env[chave.strip()] = valor.strip().strip('"').strip("'")
    faltando = [k for k in ("FTP_HOST", "FTP_USER", "FTP_PASS") if not env.get(k)]
    if faltando:
        parar(f"preencha em site/.env: {', '.join(faltando)}")
    return env


def conectar(env: dict[str, str]) -> FTPS:
    ftp = FTPS(context=ssl.create_default_context(), timeout=60)
    try:
        ftp.connect(env["FTP_HOST"], 21)
        ftp.login(env["FTP_USER"], env["FTP_PASS"])
    except ssl.SSLCertVerificationError as erro:
        parar(f"o certificado TLS não confere com {env['FTP_HOST']} ({erro.verify_message}).\n"
              "Use em FTP_HOST o nome do servidor que aparece no cPanel (ex.: gatorXXXX.hostgator.com).")
    except ftplib.all_errors as erro:
        parar(f"não consegui entrar no FTP: {erro}")
    ftp.prot_p()
    return ftp


def listar_servidor(ftp: FTPS, pastas: set[str]) -> tuple[dict[str, tuple[int, datetime]], set[str]]:
    """Arquivos (tamanho, modificação em UTC) e pastas que já existem no servidor.
    Só desce nas pastas que o site tem, e nunca nas protegidas."""
    arquivos: dict[str, tuple[int, datetime]] = {}
    existentes: set[str] = set()

    def descer(pasta: str) -> None:
        try:
            itens = list(ftp.mlsd(pasta or ".", facts=["type", "size", "modify"]))
        except ftplib.error_perm:
            return
        for nome, fatos in itens:
            if nome in (".", ".."):
                continue
            caminho = f"{pasta}/{nome}" if pasta else nome
            if fatos.get("type") == "dir":
                existentes.add(caminho)
                if caminho in pastas and not caminho.startswith(PROTEGIDOS):
                    descer(caminho)
            elif fatos.get("type") == "file":
                quando = datetime.strptime(fatos["modify"][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                arquivos[caminho] = (int(fatos.get("size", -1)), quando)

    descer("")
    return arquivos, existentes


def precisa_enviar(local: Path, remoto: tuple[int, datetime] | None) -> str | None:
    if remoto is None:
        return "novo"
    tamanho, quando = remoto
    st = local.stat()
    if st.st_size != tamanho:
        return "alterado"
    if datetime.fromtimestamp(st.st_mtime, timezone.utc) > quando:
        return "alterado"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="só lista o que seria enviado")
    args = parser.parse_args()

    conferir_git()
    env = ler_env()
    locais = arquivos_locais()
    pastas = set()
    for caminho in locais:
        partes = caminho.split("/")[:-1]
        pastas |= {"/".join(partes[:i]) for i in range(1, len(partes) + 1)}

    print(f"{len(locais):,} arquivos de produção. Lendo o servidor {env['FTP_HOST']}…")
    ftp = conectar(env)
    remotos, existentes = listar_servidor(ftp, pastas)

    envios = [(c, p, motivo) for c, p in locais.items()
              if (motivo := precisa_enviar(p, remotos.get(c)))]
    if not envios:
        print("Nada a enviar: o servidor já está igual.")
        ftp.quit()
        return

    total = sum(p.stat().st_size for _, p, _ in envios)
    if args.dry_run:
        for caminho, _, motivo in envios:
            print(f"  {motivo:9} {caminho}")
        print(f"\n--dry-run: {len(envios):,} arquivo(s), {total / 1e6:,.1f} MB seriam enviados. Nada foi enviado.")
        ftp.quit()
        return

    for pasta in sorted(pastas - existentes, key=lambda p: p.count("/")):
        ftp.mkd(pasta)
        print(f"  pasta     {pasta}/")

    for i, (caminho, local, motivo) in enumerate(envios, 1):
        for tentativa in (1, 2):
            try:
                with open(local, "rb") as f:
                    ftp.storbinary(f"STOR {caminho}", f)
                break
            except ftplib.all_errors as erro:
                if tentativa == 2:
                    parar(f"falhou ao enviar {caminho}: {erro}. Rode de novo: o que já foi não é reenviado.")
                ftp = conectar(env)  # a conexão caiu: reconecta e tenta mais uma vez
        if len(envios) <= 200 or i % 200 == 0 or i == len(envios):
            print(f"  [{i:,}/{len(envios):,}] {motivo:9} {caminho}")

    ftp.quit()
    print(f"\nPronto: {len(envios):,} arquivo(s), {total / 1e6:,.1f} MB enviados.")


if __name__ == "__main__":
    main()

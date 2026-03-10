import builtins
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

AUTH_URL = (
    "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
    "?response_type=code"
    "&client_id=pje-trf3-1g"
    "&redirect_uri=https%3A%2F%2Fpje1g.trf3.jus.br%2Fpje%2Flogin.seam"
    "&state=fc0ede7a-4d9d-480f-8192-9e3004b1ced4"
    "&login=true"
    "&scope=openid"
)
QUADRO_AVISO_URL = "https://pje1g.trf3.jus.br/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam"
CONSULTA_URL = "https://pje1g.trf3.jus.br/pje/Processo/ConsultaProcesso/listView.seam"

PROCESSOS_FILE = Path("processos.txt")
DOWNLOAD_DIR = Path("processos_baixados")
LOG_FILE = Path("acesso.log")

SEL_NUMERO_SEQUENCIAL = "[id='fPP:numeroProcesso:numeroSequencial']"
SEL_NUMERO_DV = "[id='fPP:numeroProcesso:numeroDigitoVerificador']"
SEL_NUMERO_ANO = "[id='fPP:numeroProcesso:Ano']"
SEL_RAMO = "[id='fPP:numeroProcesso:ramoJustica']"
SEL_TRIBUNAL = "[id='fPP:numeroProcesso:respectivoTribunal']"
SEL_ORGAO = "[id='fPP:numeroProcesso:NumeroOrgaoJustica']"
SEL_SEARCH = "[id='fPP:searchProcessos']"
SEL_DOWNLOAD_VISIBLE = "[id='navbar:j_id218']"
SEL_DOWNLOAD_HIDDEN = "[id='navbar:downloadProcesso']"

ORIGINAL_PRINT = builtins.print


def log_message(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    ORIGINAL_PRINT(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def attach_page_debug_logging(page) -> None:
    page.on("framenavigated", lambda frame: log_message(f"NAVIGATED: {frame.url}"))

    def _request(req):
        if req.resource_type in {"document", "xhr", "fetch"}:
            log_message(f"REQUEST {req.method} {req.url}")

    def _response(resp):
        req = resp.request
        if req.resource_type in {"document", "xhr", "fetch"}:
            log_message(f"RESPONSE {resp.status} {req.method} {req.url}")

    def _request_failed(req):
        log_message(f"REQUEST_FAILED {req.method} {req.url} reason={req.failure}")

    page.on("request", _request)
    page.on("response", _response)
    page.on("requestfailed", _request_failed)


def attach_dialog_auto_accept(page) -> None:
    page.on("dialog", lambda dialog: dialog.accept())


def parse_numero_processo(numero: str) -> tuple[str, str, str, str, str, str]:
    match = re.fullmatch(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", numero)
    if not match:
        raise ValueError(f"Número inválido: {numero}")
    return match.groups()


def load_processos(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Arquivo não encontrado: {path}")

    processos: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parse_numero_processo(line)
        processos.append(line)

    if not processos:
        raise ValueError("processos.txt está vazio")
    return processos


def perform_login_and_wait_otp(page) -> None:
    page.goto(AUTH_URL, wait_until="domcontentloaded", timeout=120000)

    cert_button = page.locator("#kc-pje-office")
    cert_button.wait_for(state="visible", timeout=60000)
    log_message("Clicando em CERTIFICADO DIGITAL...")
    cert_button.click(timeout=15000)

    otp_input = page.locator("#otp")
    otp_input.wait_for(state="visible", timeout=90000)

    otp_code = input("Digite o OTP: ").strip()
    while not otp_code:
        otp_code = input("OTP vazio. Digite o OTP: ").strip()

    otp_input.fill(otp_code)
    otp_input.press("Enter")
    page.wait_for_load_state("networkidle", timeout=120000)
    log_message(f"OTP enviado. URL atual: {page.url}")


def ensure_post_login_pages(page) -> None:
    page.goto(QUADRO_AVISO_URL, wait_until="domcontentloaded", timeout=120000)
    log_message(f"Pós-login (Quadro de Avisos): {page.url}")

    page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=120000)
    page.locator(SEL_NUMERO_SEQUENCIAL).wait_for(state="visible", timeout=60000)
    log_message(f"Tela de consulta pronta: {page.url}")


def fill_process_fields(page, numero: str) -> None:
    sequencial, dv, ano, ramo, tribunal, orgao = parse_numero_processo(numero)
    page.fill(SEL_NUMERO_SEQUENCIAL, sequencial)
    page.fill(SEL_NUMERO_DV, dv)
    page.fill(SEL_NUMERO_ANO, ano)
    page.fill(SEL_RAMO, ramo)
    page.fill(SEL_TRIBUNAL, tribunal)
    page.fill(SEL_ORGAO, orgao)


def search_processo(page, numero: str) -> None:
    sequencial, *_ = parse_numero_processo(numero)
    button = page.locator(SEL_SEARCH)
    button.wait_for(state="visible", timeout=60000)

    def is_target_response(response) -> bool:
        req = response.request
        post_data = req.post_data or ""
        return (
            req.method == "POST"
            and "ConsultaProcesso/listView.seam" in response.url
            and (
                f"fPP%3AnumeroProcesso%3AnumeroSequencial={sequencial}" in post_data
                or f"fPP:numeroProcesso:numeroSequencial={sequencial}" in post_data
            )
        )

    with page.expect_response(is_target_response, timeout=30000):
        button.click(timeout=10000)


def open_result(page, numero: str):
    result = page.locator(f"a.btn-link.btn-condensed[title='{numero}']")
    if result.count() == 0:
        raise ValueError(f"Resultado não encontrado para {numero}")

    result.first.wait_for(state="visible", timeout=60000)

    try:
        with page.expect_popup(timeout=20000) as popup_info:
            result.first.click(timeout=10000)
        detail = popup_info.value
        detail.wait_for_load_state("domcontentloaded", timeout=120000)
        return detail
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=120000)
        return page


def trigger_download(detail_page) -> Path | None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    menu_download = detail_page.locator("a.btn-menu-abas.dropdown-toggle[title='Download autos do processo']")
    menu_download.wait_for(state="visible", timeout=60000)
    menu_download.click(timeout=10000)

    btn = detail_page.locator(SEL_DOWNLOAD_VISIBLE)
    if btn.count() == 0:
        btn = detail_page.locator(SEL_DOWNLOAD_HIDDEN)
    btn.first.wait_for(state="visible", timeout=30000)

    try:
        with detail_page.expect_download(timeout=120000) as download_info:
            btn.first.click(timeout=10000)
        download = download_info.value
        file_name = download.suggested_filename
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"
        target = DOWNLOAD_DIR / file_name
        download.save_as(str(target))
        return target
    except PlaywrightTimeoutError:
        modal = detail_page.locator("#panelAlertContentTable")
        if modal.count() > 0 and "área de download" in modal.first.inner_text().lower():
            log_message("Download agendado na Área de Download (sem arquivo imediato).")
            return None
        raise


def main() -> int:
    LOG_FILE.write_text("", encoding="utf-8")
    log_message(f"Iniciando automação TRF3. URL login: {AUTH_URL}")
    processos = load_processos(PROCESSOS_FILE)

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        context = browser.new_context(accept_downloads=True, ignore_https_errors=True)
        page = context.new_page()
        attach_dialog_auto_accept(page)
        attach_page_debug_logging(page)

        try:
            perform_login_and_wait_otp(page)
            ensure_post_login_pages(page)

            for i, numero in enumerate(processos, start=1):
                detail = None
                log_message(f"[{i}/{len(processos)}] Consultando processo: {numero}")
                try:
                    page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=120000)
                    fill_process_fields(page, numero)
                    search_processo(page, numero)
                    detail = open_result(page, numero)
                    pdf = trigger_download(detail)
                    if pdf:
                        log_message(f"Download concluído: {pdf}")
                    else:
                        log_message(f"Processo {numero}: geração assíncrona na Área de Download")
                except (PlaywrightTimeoutError, PlaywrightError, ValueError) as exc:
                    log_message(f"Falha em {numero}: {exc}")
                finally:
                    if detail and detail != page:
                        try:
                            detail.close()
                        except PlaywrightError:
                            pass

            input("Fluxo finalizado. Pressione ENTER para encerrar... ")
            return 0
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())

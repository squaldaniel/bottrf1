import json
import re
import sys
from dataclasses import dataclass
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
DEBUG_PROFILE_DIR = Path(".playwright-firefox-profile")
DEBUG_STORAGE_STATE_FILE = Path(".playwright-debug-storage-state.json")
SESSION_COOKIES_FILE = Path("session_cookies.json")

SEL_NUMERO_SEQUENCIAL = "[id='fPP:numeroProcesso:numeroSequencial']"
SEL_NUMERO_DV = "[id='fPP:numeroProcesso:numeroDigitoVerificador']"
SEL_NUMERO_ANO = "[id='fPP:numeroProcesso:Ano']"
SEL_RAMO = "[id='fPP:numeroProcesso:ramoJustica']"
SEL_TRIBUNAL = "[id='fPP:numeroProcesso:respectivoTribunal']"
SEL_ORGAO = "[id='fPP:numeroProcesso:NumeroOrgaoJustica']"
SEL_SEARCH = "[id='fPP:searchProcessos']"
SEL_DOWNLOAD_VISIBLE = "[id='navbar:j_id218']"
SEL_DOWNLOAD_HIDDEN = "[id='navbar:downloadProcesso']"

IMPORTANT_COOKIE_NAMES = {
    "AUTH_SESSION_ID",
    "AUTH_SESSION_ID_LEGACY",
    "KEYCLOAK_IDENTITY",
    "KEYCLOAK_IDENTITY_LEGACY",
    "KEYCLOAK_SESSION",
    "KEYCLOAK_SESSION_LEGACY",
    "AWSALB",
    "AWSALBCORS",
    "JSESSIONID",
}


@dataclass(frozen=True)
class AppConfig:
    debug: bool


def log_message(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "tree", "yes", "y", "on"}


def parse_app_config(argv: list[str]) -> AppConfig:
    debug = False
    for arg in argv:
        normalized = arg.strip().lower()
        if normalized == "--debug":
            debug = True
        elif normalized.startswith("--debug="):
            debug = parse_bool(normalized.split("=", 1)[1])
    return AppConfig(debug=debug)


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
    page.on("dialog", lambda dialog: dialog.accept())


def is_firefox_profile_in_use(profile_dir: Path) -> bool:
    return any((profile_dir / x).exists() for x in ["parent.lock", "lock", ".parentlock"])


def create_context(playwright, config: AppConfig):
    browser = None
    if config.debug:
        DEBUG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            context = playwright.firefox.launch_persistent_context(
                user_data_dir=str(DEBUG_PROFILE_DIR),
                headless=False,
                accept_downloads=True,
                ignore_https_errors=True,
            )
            log_message(f"Modo debug ativo com perfil persistente: {DEBUG_PROFILE_DIR}")
            return browser, context
        except PlaywrightError:
            if not is_firefox_profile_in_use(DEBUG_PROFILE_DIR):
                raise
            log_message("Perfil em uso. Abrindo nova janela com storage_state salvo.")
            browser = playwright.firefox.launch(headless=False)
            storage = str(DEBUG_STORAGE_STATE_FILE) if DEBUG_STORAGE_STATE_FILE.exists() else None
            context = browser.new_context(
                accept_downloads=True,
                ignore_https_errors=True,
                storage_state=storage,
            )
            return browser, context

    browser = playwright.firefox.launch(headless=False)
    context = browser.new_context(accept_downloads=True, ignore_https_errors=True)
    return browser, context


def save_session_artifacts(context) -> None:
    DEBUG_STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(DEBUG_STORAGE_STATE_FILE))

    cookies = [c for c in context.cookies() if c.get("name") in IMPORTANT_COOKIE_NAMES]
    SESSION_COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    log_message(
        "Sessão salva. Cookies importantes: "
        + ", ".join(sorted({c['name'] for c in cookies}))
    )


def get_or_create_page(context):
    for p in reversed(context.pages):
        if not p.is_closed():
            attach_page_debug_logging(p)
            log_message(f"Reaproveitando janela aberta: {p.url}")
            return p
    page = context.new_page()
    attach_page_debug_logging(page)
    return page


def goto_with_retry(page, url: str, wait_until: str = "domcontentloaded", attempts: int = 4) -> None:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=120000)
            return
        except PlaywrightError as exc:
            last_exc = exc
            if "NS_ERROR_NET_INTERRUPT" in str(exc) and attempt < attempts:
                log_message(f"Falha transitória ao abrir {url} (tentativa {attempt}/{attempts}).")
                page.wait_for_timeout(1500)
                continue
            raise
    if last_exc:
        raise last_exc


def is_consulta_ready(page) -> bool:
    try:
        return (
            "ConsultaProcesso/listView.seam" in (page.url or "")
            and page.locator(SEL_NUMERO_SEQUENCIAL).count() > 0
        )
    except PlaywrightError:
        return False


def login_with_otp(page, context) -> None:
    goto_with_retry(page, AUTH_URL)
    page.locator("#kc-pje-office").wait_for(state="visible", timeout=60000)
    log_message("Clicando em CERTIFICADO DIGITAL...")
    page.locator("#kc-pje-office").click(timeout=15000)

    otp_input = page.locator("#otp")
    otp_input.wait_for(state="visible", timeout=90000)

    otp = input("Digite o OTP: ").strip()
    while not otp:
        otp = input("OTP vazio. Digite o OTP: ").strip()

    otp_input.fill(otp)
    otp_input.press("Enter")
    page.wait_for_load_state("networkidle", timeout=120000)
    log_message(f"OTP enviado. URL atual: {page.url}")

    goto_with_retry(page, CONSULTA_URL)
    page.locator(SEL_NUMERO_SEQUENCIAL).wait_for(state="visible", timeout=60000)
    save_session_artifacts(context)


def ensure_logged_in_and_ready(page, context):
    if is_consulta_ready(page):
        log_message("Sessão reaproveitada na janela já aberta. Indo direto para consulta.")
        return

    try:
        goto_with_retry(page, CONSULTA_URL)
        page.locator(SEL_NUMERO_SEQUENCIAL).wait_for(state="visible", timeout=10000)
        log_message("Sessão reaproveitada via cookies/perfil. Consulta pronta.")
        save_session_artifacts(context)
        return
    except (PlaywrightError, PlaywrightTimeoutError):
        log_message("Sessão não reaproveitada. Solicitando OTP...")

    login_with_otp(page, context)


def fill_process_fields(page, numero: str) -> None:
    s, dv, ano, ramo, trib, org = parse_numero_processo(numero)
    page.fill(SEL_NUMERO_SEQUENCIAL, s)
    page.fill(SEL_NUMERO_DV, dv)
    page.fill(SEL_NUMERO_ANO, ano)
    page.fill(SEL_RAMO, ramo)
    page.fill(SEL_TRIBUNAL, trib)
    page.fill(SEL_ORGAO, org)


def search_processo(page, numero: str) -> None:
    sequencial, *_ = parse_numero_processo(numero)

    def is_target_response(response) -> bool:
        req = response.request
        data = req.post_data or ""
        return (
            req.method == "POST"
            and "ConsultaProcesso/listView.seam" in response.url
            and (
                f"fPP%3AnumeroProcesso%3AnumeroSequencial={sequencial}" in data
                or f"fPP:numeroProcesso:numeroSequencial={sequencial}" in data
            )
        )

    with page.expect_response(is_target_response, timeout=30000):
        page.locator(SEL_SEARCH).click(timeout=10000)


def open_result(page, numero: str):
    result = page.locator(f"a.btn-link.btn-condensed[title='{numero}']")
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
    detail_page.locator("a.btn-menu-abas.dropdown-toggle[title='Download autos do processo']").click(timeout=10000)
    btn = detail_page.locator(SEL_DOWNLOAD_VISIBLE)
    if btn.count() == 0:
        btn = detail_page.locator(SEL_DOWNLOAD_HIDDEN)
    btn.first.wait_for(state="visible", timeout=30000)

    try:
        with detail_page.expect_download(timeout=120000) as dlinfo:
            btn.first.click(timeout=10000)
        dl = dlinfo.value
        filename = dl.suggested_filename
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        target = DOWNLOAD_DIR / filename
        dl.save_as(str(target))
        return target
    except PlaywrightTimeoutError:
        modal = detail_page.locator("#panelAlertContentTable")
        if modal.count() > 0 and "área de download" in modal.first.inner_text().lower():
            log_message("Download assíncrono. Verifique Área de Download.")
            return None
        raise


def keep_debug_window_alive(context) -> None:
    log_message("Modo debug: janela mantida aberta. Digite 'sair' para encerrar. Ctrl+C será ignorado.")
    while True:
        try:
            cmd = input("[debug] Digite 'sair' para fechar: ").strip().lower()
        except KeyboardInterrupt:
            log_message("Ctrl+C ignorado em modo debug.")
            continue
        if cmd in {"sair", "exit", "quit"}:
            return


def main() -> int:
    LOG_FILE.write_text("", encoding="utf-8")
    config = parse_app_config(sys.argv[1:])
    processos = load_processos(PROCESSOS_FILE)
    log_message(f"Iniciando automação TRF3. URL login: {AUTH_URL}")
    log_message(f"Modo debug: {'ativo' if config.debug else 'inativo'}")

    with sync_playwright() as p:
        browser = None
        context = None
        try:
            browser, context = create_context(p, config)
            page = get_or_create_page(context)
            ensure_logged_in_and_ready(page, context)

            for i, numero in enumerate(processos, start=1):
                detail = None
                try:
                    log_message(f"[{i}/{len(processos)}] Consultando processo: {numero}")
                    goto_with_retry(page, CONSULTA_URL)
                    fill_process_fields(page, numero)
                    search_processo(page, numero)
                    detail = open_result(page, numero)
                    out = trigger_download(detail)
                    log_message(f"Download {'concluído: ' + str(out) if out else 'agendado na Área de Download'}")
                except (PlaywrightError, PlaywrightTimeoutError, ValueError) as exc:
                    log_message(f"Falha em {numero}: {exc}")
                finally:
                    if detail and detail != page:
                        try:
                            detail.close()
                        except PlaywrightError:
                            pass

            save_session_artifacts(context)

            if config.debug:
                keep_debug_window_alive(context)
            else:
                input("Fluxo finalizado. Pressione ENTER para encerrar... ")

            return 0
        except KeyboardInterrupt:
            if config.debug and context:
                keep_debug_window_alive(context)
                return 0
            return 0
        finally:
            if context:
                save_session_artifacts(context)
            if config.debug:
                log_message("Modo debug ativo: fechamento automático desabilitado.")
            else:
                if context:
                    context.close()
                if browser:
                    browser.close()


if __name__ == "__main__":
    sys.exit(main())

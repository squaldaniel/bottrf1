import builtins
import os
import re
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

AUTH_URL = (
    "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
    "?response_type=code"
    "&client_id=pje-trf1-1g"
    "&redirect_uri=https%3A%2F%2Fpje1g.trf1.jus.br%2Fpje%2Flogin.seam"
    "&state=54cf8d8f-d065-47ad-8646-fc66deeacaab"
    "&login=true"
    "&scope=openid"
)
CONSULTA_URL = "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/listView.seam"
PROCESSO_NUMERO = "1000237-96.2026.4.01.3700"

RESPONSE_DUMP_FILE = "ajax_response_dump.txt"
LOG_FILE = Path("acesso.log")
DOWNLOAD_DIR = Path("processos_baixados")
DEBUG_PROFILE_DIR = Path(".playwright-firefox-profile")
DEBUG_STORAGE_STATE_FILE = Path(".playwright-debug-storage-state.json")

SEL_NUMERO_SEQUENCIAL = "[id='fPP:numeroProcesso:numeroSequencial']"
SEL_NUMERO_DV = "[id='fPP:numeroProcesso:numeroDigitoVerificador']"
SEL_NUMERO_ANO = "[id='fPP:numeroProcesso:Ano']"
SEL_RAMO = "[id='fPP:numeroProcesso:ramoJustica']"
SEL_TRIBUNAL = "[id='fPP:numeroProcesso:respectivoTribunal']"
SEL_ORGAO = "[id='fPP:numeroProcesso:NumeroOrgaoJustica']"
SEL_SEARCH_PROCESSOS = "[id='fPP:searchProcessos']"
SEL_DOWNLOAD_PROCESSO = "[id='navbar:downloadProcesso']"
SEL_ALERTA_CERTIFICADO_POPUP = "#popupAlertaCertificadoProximoDeExpirarContentDiv"

ORIGINAL_PRINT = builtins.print

COOKIE_NAMES = [
    "AUTH_SESSION_ID_LEGACY",
    "AUTH_SESSION_ID",
    "AWSALBCORS",
    "AWSALB",
    "KC_RESTART",
    "KEYCLOAK_IDENTITY_LEGACY",
    "KEYCLOAK_IDENTITY",
    "KEYCLOAK_SESSION_LEGACY",
    "KEYCLOAK_SESSION",
]


def log_message(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    ORIGINAL_PRINT(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def attach_page_debug_logging(page) -> None:
    page.on("framenavigated", lambda frame: log_message(f"NAVIGATED: {frame.url}"))

    def _request(req):
        if req.resource_type in {"xhr", "fetch", "document"}:
            log_message(f"REQUEST {req.method} {req.url}")

    def _response(resp):
        req = resp.request
        if req.resource_type in {"xhr", "fetch", "document"}:
            log_message(f"RESPONSE {resp.status} {req.method} {resp.url}")

    def _request_failed(req):
        failure_text = req.failure or "unknown"
        log_message(f"REQUEST_FAILED {req.method} {req.url} reason={failure_text}")

    page.on("request", _request)
    page.on("response", _response)
    page.on("requestfailed", _request_failed)


def navigate_to_consulta_page(page, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        log_message(f"Tentativa {attempt}/{attempts} de ir para tela de consulta: {CONSULTA_URL}")
        page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.locator(SEL_NUMERO_SEQUENCIAL).wait_for(state="visible", timeout=15000)
            log_message(f"Tela de consulta pronta. URL final: {page.url}")
            return
        except PlaywrightTimeoutError:
            log_message(f"Campo de consulta não visível após tentativa {attempt}. URL atual: {page.url}")

            if is_bad_request_page(page):
                raise ValueError(
                    "Portal retornou 'Bad Request' ao tentar abrir a consulta após OTP. "
                    "Refaça login/autenticação e tente novamente."
                )

    raise ValueError(
        "Não foi possível abrir a tela de consulta após autenticação. "
        f"URL atual: {page.url}"
    )



def install_print_logger() -> None:
    def _logged_print(*args, **kwargs):
        ORIGINAL_PRINT(*args, **kwargs)

        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        message = sep.join(str(arg) for arg in args) + end

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(message)

    builtins.print = _logged_print


@dataclass(frozen=True)
class AppConfig:
    debug: bool


def parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_app_config(argv: list[str]) -> AppConfig:
    debug = False

    for arg in argv:
        normalized = arg.strip().lower()
        if normalized == "--debug":
            debug = True
            continue

        if normalized.startswith("--debug="):
            debug = parse_bool(normalized.split("=", 1)[1])
            continue

        if normalized.startswith("debug="):
            debug = parse_bool(normalized.split("=", 1)[1])

    return AppConfig(debug=debug)


def extract_autenticar_args(onclick: str) -> tuple[str, str] | None:
    match = re.search(r"autenticar\('([^']+)'\s*,\s*'([^']+)'\)", onclick)
    if not match:
        return None
    return match.group(1), match.group(2)


def parse_numero_processo(numero: str) -> tuple[str, str, str, str, str, str]:
    match = re.fullmatch(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", numero)
    if not match:
        raise ValueError(
            "Número de processo inválido. Formato esperado: NNNNNNN-DD.AAAA.J.TR.OOOO"
        )
    return match.groups()


def show_missing_browser_help() -> None:
    print("\nPlaywright está instalado, mas o navegador Firefox não foi baixado.")
    print("Execute um dos comandos abaixo e tente novamente:\n")
    print("  python -m playwright install firefox")
    print("  playwright install firefox")


def load_env_cookies() -> list[dict]:
    cookies = []

    for name in COOKIE_NAMES:
        value = os.getenv(name)
        if not value or value == "Array":
            continue

        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": "sso.cloud.pje.jus.br",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        )
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": "pje1g.trf1.jus.br",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        )

    return cookies

def apply_cookies_if_available(context) -> None:
    cookies = load_env_cookies()
    if not cookies:
        print("Nenhum cookie de sessão encontrado em variáveis de ambiente.")
        return

    context.add_cookies(cookies)
    print(f"Cookies de sessão carregados do ambiente: {len(cookies)} entradas.")




def get_debug_storage_state() -> str | None:
    if not DEBUG_STORAGE_STATE_FILE.exists():
        return None

    print(f"Estado de sessão debug detectado em: {DEBUG_STORAGE_STATE_FILE}")
    return str(DEBUG_STORAGE_STATE_FILE)


def save_debug_storage_state(context) -> None:
    DEBUG_STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(DEBUG_STORAGE_STATE_FILE))
    print(f"Estado de sessão debug salvo em: {DEBUG_STORAGE_STATE_FILE}")


def try_save_debug_storage_state(context) -> None:
    if not context:
        return

    try:
        save_debug_storage_state(context)
    except KeyboardInterrupt:
        log_message("Salvamento do estado debug interrompido pelo usuário (KeyboardInterrupt).")
    except PlaywrightError as exc:
        log_message(f"Não foi possível salvar estado debug: {exc}")


def is_firefox_profile_in_use(profile_dir: Path) -> bool:
    lock_files = ["parent.lock", "lock", ".parentlock"]
    return any((profile_dir / name).exists() for name in lock_files)


def create_context(playwright, config: AppConfig):
    browser = None

    if config.debug:
        DEBUG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        profile_in_use = is_firefox_profile_in_use(DEBUG_PROFILE_DIR)
        if profile_in_use:
            print(
                "Perfil do Firefox de debug aparenta estar em uso. "
                "Tentando aproveitar o estado salvo de sessão."
            )

        try:
            context = playwright.firefox.launch_persistent_context(
                user_data_dir=str(DEBUG_PROFILE_DIR),
                headless=False,
                accept_downloads=True,
            )
            print(f"Modo debug ativo. Perfil persistente: {DEBUG_PROFILE_DIR}")
            return browser, context
        except PlaywrightError as exc:
            if not profile_in_use:
                raise

            print(
                "Não foi possível abrir novo contexto persistente porque o perfil está em uso. "
                "Reutilizando storage_state salvo no modo debug."
            )
            browser = playwright.firefox.launch(headless=False)
            context = browser.new_context(
                accept_downloads=True,
                storage_state=get_debug_storage_state(),
            )
            return browser, context

    browser = playwright.firefox.launch(headless=False)
    context = browser.new_context(accept_downloads=True)
    return browser, context
def attach_dialog_auto_accept(page) -> None:
    def _handler(dialog):
        print(f"Dialog detectado: {dialog.message[:120]}...")
        dialog.accept()

    page.on("dialog", _handler)


def is_logged_in(page) -> bool:
    page.goto(CONSULTA_URL, wait_until="networkidle", timeout=60000)

    if "ConsultaProcesso/listView.seam" not in page.url:
        return False

    consulta_input = page.locator(SEL_NUMERO_SEQUENCIAL)
    return consulta_input.count() > 0


def perform_login_flow(page) -> None:
    page.goto(AUTH_URL, wait_until="networkidle", timeout=60000)

    cert_button = page.locator("#kc-pje-office")
    cert_button.wait_for(state="visible", timeout=60000)

    onclick = cert_button.get_attribute("onclick") or ""
    parsed = extract_autenticar_args(onclick)

    if parsed:
        token, random_value = parsed
        print("Parâmetros encontrados no onclick de autenticar:")
        print(f"  token: {token[:20]}...{token[-20:]}")
        print(f"  random: {random_value}")
    else:
        print("Não foi possível parsear os parâmetros de autenticar no atributo onclick.")

    log_message("Chamando 'CERTIFICADO DIGITAL'...")
    cert_button.click()

    otp_input = page.locator("#otp")
    otp_input.wait_for(state="visible", timeout=60000)

    otp_code = input("Digite o código OTP para continuar: ").strip()
    while not otp_code:
        otp_code = input("Código vazio. Digite o OTP: ").strip()

    otp_input.fill(otp_code)

    # Em alguns fluxos o OTP só é processado após ENTER ou submit explícito.
    otp_input.press("Enter")
    page.wait_for_load_state("networkidle", timeout=60000)
    log_message(f"OTP enviado com sucesso. URL atual após envio: {page.url}")
    navigate_to_consulta_page(page)



def ensure_consulta_page_ready(page) -> None:
    close_certificado_alert_popup_if_present(page)
    navigate_to_consulta_page(page)


def close_certificado_alert_popup_if_present(page) -> bool:
    popup = page.locator(SEL_ALERTA_CERTIFICADO_POPUP)
    if popup.count() == 0:
        return False

    close_button = popup.locator("span.btn-fechar")
    if close_button.count() == 0:
        print(
            "Popup de alerta de certificado foi detectado, "
            "mas o botão de fechar não foi encontrado."
        )
        return False

    print("Popup de certificado próximo de expirar detectado. Fechando alerta...")
    close_button.first.click()

    try:
        popup.wait_for(state="hidden", timeout=5000)
    except PlaywrightTimeoutError:
        print("Popup de certificado não ocultou no tempo esperado; seguindo fluxo.")

    return True



def go_to_consulta_via_processo_link(page) -> None:
    close_certificado_alert_popup_if_present(page)

    processo_link = page.locator("a[href='/pje/Processo/ConsultaProcesso/listView.seam']")

    if processo_link.count() > 0:
        try:
            processo_link.first.wait_for(state="visible", timeout=10000)
            log_message("Link 'Processo' encontrado. Acessando tela de consulta via menu...")
            processo_link.first.click()
            page.wait_for_load_state("networkidle", timeout=60000)
            close_certificado_alert_popup_if_present(page)
            navigate_to_consulta_page(page)
            return
        except PlaywrightTimeoutError:
            log_message("Link 'Processo' não ficou clicável; usando URL direta da consulta.")

    log_message("Link 'Processo' não apareceu no tempo esperado. Usando fallback para URL direta da consulta...")
    navigate_to_consulta_page(page)
    close_certificado_alert_popup_if_present(page)

def fill_numero_processo_fields(page, numero: str) -> None:
    sequencial, dv, ano, ramo, tribunal, orgao = parse_numero_processo(numero)

    print("Preenchendo campos do número do processo:")
    print(
        f"  sequencial={sequencial}, dv={dv}, ano={ano}, ramo={ramo}, tribunal={tribunal}, orgao={orgao}"
    )

    page.locator(SEL_NUMERO_SEQUENCIAL).wait_for(state="visible", timeout=60000)
    page.fill(SEL_NUMERO_SEQUENCIAL, sequencial)
    page.fill(SEL_NUMERO_DV, dv)
    page.fill(SEL_NUMERO_ANO, ano)
    page.fill(SEL_RAMO, ramo)
    page.fill(SEL_TRIBUNAL, tribunal)
    page.fill(SEL_ORGAO, orgao)


def is_bad_request_page(page) -> bool:
    try:
        title = (page.title() or "").strip().lower()
    except PlaywrightError:
        title = ""

    if "bad request" in title:
        return True

    return page.get_by_text("Bad Request", exact=False).first.count() > 0


def trigger_search_and_capture_ajax(page, numero: str) -> None:
    sequencial, _, _, _, _, _ = parse_numero_processo(numero)

    search_button = page.locator(SEL_SEARCH_PROCESSOS)
    search_button.wait_for(state="visible", timeout=60000)

    def is_target_response(response) -> bool:
        request = response.request
        post_data = request.post_data or ""
        has_seq_key = (
            "fPP%3AnumeroProcesso%3AnumeroSequencial=" in post_data
            or "fPP:numeroProcesso:numeroSequencial=" in post_data
        )
        has_seq_value = (
            f"fPP%3AnumeroProcesso%3AnumeroSequencial={sequencial}" in post_data
            or f"fPP:numeroProcesso:numeroSequencial={sequencial}" in post_data
        )
        has_search_flag = "fPP%3AsearchProcessos=" in post_data or "fPP:searchProcessos=" in post_data
        return (
            request.method == "POST"
            and "ConsultaProcesso/listView.seam" in response.url
            and has_seq_key
            and has_seq_value
            and has_search_flag
        )

    print("Disparando consulta (fPP:searchProcessos) e aguardando resposta AJAX...")
    status_info = "desconhecido"
    try:
        with page.expect_response(is_target_response, timeout=60000) as response_info:
            search_button.click()
        response = response_info.value
        body_text = response.text()
        status_info = str(response.status)
    except PlaywrightTimeoutError:
        print(
            "Não foi possível capturar a resposta AJAX esperada dentro do tempo limite. "
            "Continuando com fallback baseado no DOM atual."
        )
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        if is_bad_request_page(page):
            raise ValueError(
                "Portal retornou 'Bad Request' após a autenticação/consulta. "
                "Tente novamente; se persistir, reinicie a sessão de debug e autentique de novo."
            )

        body_text = page.content()
        status_info = "fallback-dom"

    Path(RESPONSE_DUMP_FILE).write_text(body_text, encoding="utf-8")
    print(f"Resposta de consulta capturada com status: {status_info}")
    print(f"Resposta salva em: {RESPONSE_DUMP_FILE}")
    print("Trecho da resposta (primeiros 500 caracteres):")
    print(body_text[:500])


def open_process_result(page, numero_processo: str):
    result_link = page.locator(
        f"a[id^='fPP:processosTable:'][id$=':j_id509'][title='{numero_processo}']"
    )

    if result_link.count() == 0:
        print(
            "Link exato do processo não encontrado pelo title; "
            "usando primeiro resultado da tabela como fallback."
        )
        result_link = page.locator("a[id^='fPP:processosTable:'][id$=':j_id509']").first

    result_link.wait_for(state="visible", timeout=60000)

    titulo = result_link.get_attribute("title") or "(sem título)"
    print(f"Abrindo processo encontrado: {titulo}")

    try:
        with page.expect_popup(timeout=20000) as popup_info:
            result_link.click()
        popup_page = popup_info.value
        popup_page.wait_for_load_state("networkidle", timeout=60000)
        return popup_page
    except PlaywrightTimeoutError:
        page.wait_for_load_state("networkidle", timeout=60000)
        return page


def download_processo_pdf(detail_page) -> Path:
    attach_dialog_auto_accept(detail_page)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    menu_download = detail_page.locator("a.btn-menu-abas.dropdown-toggle[title='Download autos do processo']")
    menu_download.wait_for(state="visible", timeout=60000)
    menu_download.click()

    download_button = detail_page.locator(SEL_DOWNLOAD_PROCESSO)
    download_button.wait_for(state="visible", timeout=60000)

    print("Solicitando download do PDF dos autos...")
    with detail_page.expect_download(timeout=120000) as download_info:
        download_button.click()

    download = download_info.value
    suggested_name = download.suggested_filename
    if not suggested_name.lower().endswith(".pdf"):
        suggested_name = f"{suggested_name}.pdf"

    target_file = DOWNLOAD_DIR / suggested_name
    download.save_as(str(target_file))

    print(f"Download concluído: {target_file}")
    return target_file


def main() -> int:
    LOG_FILE.write_text("", encoding="utf-8")
    install_print_logger()
    config = parse_app_config(sys.argv[1:])
    log_message(f"Iniciando automação. URL de autenticação: {AUTH_URL}")
    log_message(f"Modo debug: {'ativo' if config.debug else 'inativo'}")

    with sync_playwright() as p:
        browser = None
        context = None

        try:
            browser, context = create_context(p, config)

            if not config.debug:
                apply_cookies_if_available(context)
            else:
                if context.cookies():
                    print("Cookies já presentes no contexto debug persistente.")
                else:
                    apply_cookies_if_available(context)

            page = context.new_page()
            attach_dialog_auto_accept(page)
            attach_page_debug_logging(page)

            if is_logged_in(page):
                log_message("Sessão válida detectada por cookies/perfil. Pulando etapa de login/OTP.")
            else:
                log_message("Sessão não autenticada. Executando login e OTP...")
                perform_login_flow(page)

            ensure_consulta_page_ready(page)

            fill_numero_processo_fields(page, PROCESSO_NUMERO)
            trigger_search_and_capture_ajax(page, PROCESSO_NUMERO)

            detail_page = open_process_result(page, PROCESSO_NUMERO)
            download_file = download_processo_pdf(detail_page)
            log_message(f"Arquivo final salvo em: {download_file}")

            if config.debug:
                try_save_debug_storage_state(context)
                log_message(
                    "Modo debug ativo: execução concluída. "
                    "Pressione ENTER para encerrar sem stack trace de KeyboardInterrupt."
                )
                input("Pressione ENTER para encerrar a aplicação... ")
                return 0

            log_message("Fluxo concluído. Aguardando próximas instruções.")
            input("Pressione ENTER para encerrar a aplicação...")

        except KeyboardInterrupt:
            if config.debug:
                try_save_debug_storage_state(context)
            log_message("Execução interrompida pelo usuário.")
            return 0
        except (PlaywrightTimeoutError, ValueError) as exc:
            log_message(f"Erro de tempo/validação: {exc}")
            return 1
        except PlaywrightError as exc:
            error_text = str(exc)
            if "Executable doesn't exist" in error_text:
                show_missing_browser_help()
            else:
                log_message(f"Erro do Playwright: {exc}")
            return 1
        finally:
            if config.debug:
                log_message("Modo debug ativo: pulando fechamento automático do navegador/contexto.")
            else:
                if context:
                    context.close()
                if browser:
                    browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

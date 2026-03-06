import os
import re
import sys
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

def load_env_cookies() -> list[dict]:

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
def download_processo_pdf(detail_page) -> Path:

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
    config = parse_app_config(sys.argv[1:])
    print(f"Iniciando automação. URL de autenticação: {AUTH_URL}")
    print(f"Modo debug: {'ativo' if config.debug else 'inativo'}")

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

            if is_logged_in(page):
                print("Sessão válida detectada por cookies/perfil. Pulando etapa de login/OTP.")
            else:
                print("Sessão não autenticada. Executando login e OTP...")
                perform_login_flow(page)
                go_to_consulta_via_processo_link(page)

            ensure_consulta_page_ready(page)

            fill_numero_processo_fields(page, PROCESSO_NUMERO)
            trigger_search_and_capture_ajax(page, PROCESSO_NUMERO)

            detail_page = open_process_result(page, PROCESSO_NUMERO)
            download_file = download_processo_pdf(detail_page)
            print(f"Arquivo final salvo em: {download_file}")

            if config.debug:
                save_debug_storage_state(context)
                print(
                    "Modo debug ativo: a janela Firefox será mantida aberta ao finalizar o script. "
                    "Use Ctrl+C para encerrar o processo quando quiser."
                )
                while True:
                    page.wait_for_timeout(60_000)

            print("Fluxo concluído. Aguardando próximas instruções.")
            input("Pressione ENTER para encerrar a aplicação...")

        except KeyboardInterrupt:
            if config.debug and context:
                save_debug_storage_state(context)
            print("Execução interrompida pelo usuário.")
            return 0
        except (PlaywrightTimeoutError, ValueError) as exc:
            print(f"Erro de tempo/validação: {exc}")
            return 1
        except PlaywrightError as exc:
            error_text = str(exc)
            if "Executable doesn't exist" in error_text:
                show_missing_browser_help()
            else:
                print(f"Erro do Playwright: {exc}")
            return 1
        finally:
            if config.debug:
                print("Modo debug ativo: pulando fechamento automático do navegador/contexto.")
            else:
                if context:
                    context.close()
                if browser:
                    browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
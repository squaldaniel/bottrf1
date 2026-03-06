import os
import re
import sys
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


def attach_dialog_auto_accept(page) -> None:
    def _handler(dialog):
        print(f"Dialog detectado: {dialog.message[:120]}...")
        dialog.accept()

    page.on("dialog", _handler)


def is_logged_in(page) -> bool:
    page.goto(CONSULTA_URL, wait_until="networkidle", timeout=60000)

    if "ConsultaProcesso/listView.seam" not in page.url:
        return False

    consulta_input = page.locator("#fPP\\:numeroProcesso\\:numeroSequencial")
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

    print("Clicando em 'CERTIFICADO DIGITAL'...")
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
    print(f"OTP enviado com sucesso. URL atual após envio: {page.url}")



def ensure_consulta_page_ready(page) -> None:
    page.goto(CONSULTA_URL, wait_until="domcontentloaded", timeout=60000)

    numero_seq_input = page.locator("#fPP\:numeroProcesso\:numeroSequencial")
    try:
        numero_seq_input.wait_for(state="visible", timeout=60000)
    except PlaywrightTimeoutError as exc:
        raise ValueError(
            "Tela de consulta não ficou disponível após login. "
            f"URL atual: {page.url}. Verifique se o OTP foi aceito e se a sessão está autenticada."
        ) from exc

def fill_numero_processo_fields(page, numero: str) -> None:
    sequencial, dv, ano, ramo, tribunal, orgao = parse_numero_processo(numero)

    print("Preenchendo campos do número do processo:")
    print(
        f"  sequencial={sequencial}, dv={dv}, ano={ano}, ramo={ramo}, tribunal={tribunal}, orgao={orgao}"
    )

    page.locator("#fPP\\:numeroProcesso\\:numeroSequencial").wait_for(state="visible", timeout=60000)
    page.fill("#fPP\\:numeroProcesso\\:numeroSequencial", sequencial)
    page.fill("#fPP\\:numeroProcesso\\:numeroDigitoVerificador", dv)
    page.fill("#fPP\\:numeroProcesso\\:Ano", ano)
    page.fill("#fPP\\:numeroProcesso\\:ramoJustica", ramo)
    page.fill("#fPP\\:numeroProcesso\\:respectivoTribunal", tribunal)
    page.fill("#fPP\\:numeroProcesso\\:NumeroOrgaoJustica", orgao)


def trigger_search_and_capture_ajax(page, numero: str) -> None:
    sequencial, _, _, _, _, _ = parse_numero_processo(numero)

    search_button = page.locator("#fPP\\:searchProcessos")
    search_button.wait_for(state="visible", timeout=60000)

    def is_target_response(response) -> bool:
        request = response.request
        post_data = request.post_data or ""
        return (
            request.method == "POST"
            and "ConsultaProcesso/listView.seam" in response.url
            and "fPP%3AnumeroProcesso%3AnumeroSequencial=" in post_data
            and f"fPP%3AnumeroProcesso%3AnumeroSequencial={sequencial}" in post_data
            and "fPP%3AsearchProcessos=" in post_data
        )

    print("Disparando consulta (fPP:searchProcessos) e aguardando resposta AJAX...")
    with page.expect_response(is_target_response, timeout=60000) as response_info:
        search_button.click()

    response = response_info.value
    body_text = response.text()

    Path(RESPONSE_DUMP_FILE).write_text(body_text, encoding="utf-8")
    print(f"Resposta AJAX capturada com status HTTP: {response.status}")
    print(f"Resposta salva em: {RESPONSE_DUMP_FILE}")
    print("Trecho da resposta (primeiros 500 caracteres):")
    print(body_text[:500])


def open_first_process_result(page):
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
        # fallback: alguns cenários podem abrir na mesma aba
        page.wait_for_load_state("networkidle", timeout=60000)
        return page


def download_processo_pdf(detail_page) -> Path:
    attach_dialog_auto_accept(detail_page)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    menu_download = detail_page.locator("a.btn-menu-abas.dropdown-toggle[title='Download autos do processo']")
    menu_download.wait_for(state="visible", timeout=60000)
    menu_download.click()

    download_button = detail_page.locator("#navbar\\:downloadProcesso")
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
    print(f"Iniciando automação. URL de autenticação: {AUTH_URL}")

    with sync_playwright() as p:
        browser = None
        context = None

        try:
            browser = p.firefox.launch(headless=False)
            context = browser.new_context(accept_downloads=True)
            apply_cookies_if_available(context)

            page = context.new_page()
            attach_dialog_auto_accept(page)

            if is_logged_in(page):
                print("Sessão válida detectada por cookies. Pulando etapa de login/OTP.")
            else:
                print("Sessão não autenticada. Executando login e OTP...")
                perform_login_flow(page)

            ensure_consulta_page_ready(page)

            fill_numero_processo_fields(page, PROCESSO_NUMERO)
            trigger_search_and_capture_ajax(page, PROCESSO_NUMERO)

            detail_page = open_first_process_result(page)
            download_file = download_processo_pdf(detail_page)
            print(f"Arquivo final salvo em: {download_file}")

            print("Fluxo concluído. Aguardando próximas instruções.")
            input("Pressione ENTER para encerrar a aplicação...")

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
            if context:
                context.close()
            if browser:
                browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

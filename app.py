import re
import sys
from urllib.parse import parse_qsl, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = (
    "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
)
DEFAULT_PARAMS = {
    "response_type": "code",
    "client_id": "pje-trf1-1g",
}


def build_url() -> str:
    parsed = urlparse(BASE_URL)
    query = dict(parse_qsl(parsed.query))
    query.update(DEFAULT_PARAMS)
    return parsed._replace(query=urlencode(query)).geturl()


def extract_autenticar_args(onclick: str) -> tuple[str, str] | None:
    match = re.search(r"autenticar\('([^']+)'\s*,\s*'([^']+)'\)", onclick)
    if not match:
        return None
    return match.group(1), match.group(2)


def show_missing_browser_help() -> None:
    print("\nPlaywright está instalado, mas o navegador Chromium não foi baixado.")
    print("Execute um dos comandos abaixo e tente novamente:\n")
    print("  python -m playwright install chromium")
    print("  playwright install chromium")


def main() -> int:
    url = build_url()
    print(f"Abrindo URL: {url}")

    with sync_playwright() as p:
        browser = None
        context = None

        try:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            page.goto(url, wait_until="networkidle", timeout=60000)

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
                print(
                    "Não foi possível parsear os parâmetros de autenticar no atributo onclick."
                )

            print("Clicando em 'CERTIFICADO DIGITAL'...")
            cert_button.click()

            otp_input = page.locator("#otp")
            otp_input.wait_for(state="visible", timeout=60000)

            otp_code = input("Digite o código OTP para continuar: ").strip()
            while not otp_code:
                otp_code = input("Código vazio. Digite o OTP: ").strip()

            otp_input.fill(otp_code)
            print(
                "OTP preenchido com sucesso. Fluxo pausado conforme solicitado; aguardando próximas instruções."
            )
            input("Pressione ENTER para encerrar a aplicação...")

        except PlaywrightTimeoutError as exc:
            print(f"Timeout ao aguardar elemento/página: {exc}")
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

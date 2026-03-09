# Automação de login PJe TRF1

Aplicação em Python que:

1. Acessa a URL de autenticação completa do PJe (com `redirect_uri`, `state`, `login` e `scope`) para direcionamento correto.
2. Localiza o botão **CERTIFICADO DIGITAL** (`#kc-pje-office`).
3. Faz parse do atributo `onclick` para extrair os parâmetros enviados à função `autenticar(...)`.
4. Clica no botão.
5. Aguarda o campo de OTP (`#otp`) na próxima tela.
6. Solicita o código OTP ao usuário e preenche o campo.

7. Após autenticar com OTP (ou reaproveitar sessão por cookies), procura e clica no link `<a href="/pje/Processo/ConsultaProcesso/listView.seam"> Processo </a>` para abrir a consulta (com fallback para URL direta).
8. Lê o arquivo `processos.txt` (um número de processo por linha) e processa cada item da lista.
9. Para cada processo, preenche os campos e dispara a consulta no botão **Pesquisar** (`#fPP:searchProcessos`).
10. Captura a resposta AJAX da consulta e salva em `ajax_response_dump.txt`.
11. Clica no resultado do processo (priorizando o `title` igual ao número pesquisado) e aceita automaticamente os popups de confirmação.
12. Abre o menu **Download autos do processo**, clica em **Download** (`#navbar:downloadProcesso`) e salva o PDF em `processos_baixados/`.

## Requisitos

- Python 3.10+ (compatível com Python 3.13.1)
- Dependências do `requirements.txt`

## Lista de dependências (`requirements.txt`)

Arquivo atual:

```text
playwright>=1.52.0
```

Instale com:

```bash
pip install -r requirements.txt
```

## Instalação

### Linux/macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py -m playwright install firefox
```

## Execução

### 1) Preparar `processos.txt`

Antes de executar, edite `processos.txt` com um número de processo por linha (linhas em branco e iniciadas com `#` são ignoradas):

```text
1000237-96.2026.4.01.3700
0000000-00.2024.4.01.0000
```

### 2) Executar

Linux/macOS:

```bash
python app.py
```

Windows (PowerShell):

```powershell
py app.py
```

### 3) Executar em modo debug (mantém sessão/janela)

Linux/macOS:

```bash
python app.py --debug=true
```

Windows (PowerShell):

```powershell
py app.py --debug=true
```

## Erro comum

Se aparecer erro semelhante a:

- `BrowserType.launch: Executable doesn't exist ...`

Significa que o Playwright está instalado, mas o navegador não foi baixado ainda. Rode:

```bash
python -m playwright install firefox
```

ou no Windows:

```powershell
py -m playwright install firefox
```


## Reuso de sessão por cookies (opcional)

Ao iniciar, o script verifica se os cookies de sessão já existem em variáveis de ambiente e tenta reutilizar a autenticação para pular OTP.

Cookies suportados:
- `AUTH_SESSION_ID_LEGACY`
- `AUTH_SESSION_ID`
- `AWSALBCORS`
- `AWSALB`
- `KC_RESTART`
- `KEYCLOAK_IDENTITY_LEGACY`
- `KEYCLOAK_IDENTITY`
- `KEYCLOAK_SESSION_LEGACY`
- `KEYCLOAK_SESSION`

Exemplo (PowerShell):

```powershell
$env:AUTH_SESSION_ID="..."
$env:KEYCLOAK_IDENTITY="..."
py app.py
```

Se os cookies forem válidos, o script abre diretamente a tela de consulta e pula login/OTP.


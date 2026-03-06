# Automação de login PJe TRF1

Aplicação em Python que:

1. Acessa a URL de autenticação do PJe.
2. Localiza o botão **CERTIFICADO DIGITAL** (`#kc-pje-office`).
3. Faz parse do atributo `onclick` para extrair os parâmetros enviados à função `autenticar(...)`.
4. Clica no botão.
5. Aguarda o campo de OTP (`#otp`) na próxima tela.
6. Solicita o código OTP ao usuário e preenche o campo, pausando em seguida para novas instruções.

## Requisitos

- Python 3.10+
- Dependências do `requirements.txt`

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Execução

```bash
python app.py
```

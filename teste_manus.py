import asyncio
import sys
import os
import re
from playwright.async_api import async_playwright

def extrair_partes_processo(processo):
    """
    Extrai as partes do número do processo no padrão CNJ:
    NNNNNNN-DD.AAAA.J.TR.OOOO
    Exemplo: 5001508-33.2022.4.03.6327
    """
    padrao = r"(\d{7})-(\d{2})\.(\d{4})\.(\d{1})\.(\d{2})\.(\d{4})"
    match = re.match(padrao, processo.strip())
    if match:
        return {
            "sequencial": match.group(1),
            "digito": match.group(2),
            "ano": match.group(3),
            "ramo": match.group(4),
            "tribunal": match.group(5),
            "orgao": match.group(6)
        }
    return None

async def run_bot():
    async with async_playwright() as p:
        print("Iniciando o Firefox...")
        browser = await p.firefox.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        login_url = "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth?response_type=code&client_id=pje-trf3-1g&redirect_uri=https%3A%2F%2Fpje1g.trf3.jus.br%2Fpje%2Flogin.seam&state=fc0ede7a-4d9d-480f-8192-9e3004b1ced4&login=true&scope=openid"
        
        print(f"Acessando: {login_url}")
        await page.goto(login_url)

        print("Clicando em 'CERTIFICADO DIGITAL'...")
        await page.click("#kc-pje-office")

        print("Aguardando solicitação de OTP (Certificado Digital via PJeOffice)...")
        otp = input("Por favor, digite o código OTP recebido (ou pressione Enter se não houver): ")
        
        if otp:
            print(f"OTP '{otp}' recebido. Tentando prosseguir...")
            # Lógica de preenchimento de OTP se o campo aparecer
        
        search_url = "https://pje1g.trf3.jus.br/pje/Processo/ConsultaProcesso/listView.seam"
        print(f"Navegando para a página de consulta: {search_url}")
        await page.goto(search_url)

        # Confirmar se está na página correta
        if page.url.startswith("https://pje1g.trf3.jus.br/pje/Processo/ConsultaProcesso/listView.seam"):
            print("Confirmação: Você está na página de Consulta Processual.")
            
            # Ler arquivo processos.txt
            caminho_arquivo = "processos.txt"
            if not os.path.exists(caminho_arquivo):
                print(f"Erro: Arquivo '{caminho_arquivo}' não encontrado.")
                # Criar um arquivo de exemplo se não existir para teste
                with open(caminho_arquivo, "w") as f:
                    f.write("# Lista de processos para consulta\n")
                    f.write("5001508-33.2022.4.03.6327\n")
                    f.write("0018861-69.2011.4.03.6130\n")
                print(f"Arquivo '{caminho_arquivo}' de exemplo criado.")

            processos_validos = []
            with open(caminho_arquivo, "r") as f:
                for linha in f:
                    linha = linha.strip()
                    if not linha or linha.startswith("#"):
                        continue
                    processos_validos.append(linha)
            
            print(f"Total de processos encontrados no arquivo: {len(processos_validos)}")

            for proc in processos_validos:
                print(f"Processando: {proc}")
                partes = extrair_partes_processo(proc)
                
                if partes:
                    # Preencher os campos conforme os IDs fornecidos
                    await page.fill('input[id="fPP:numeroProcesso:numeroSequencial"]', partes["sequencial"])
                    await page.fill('input[id="fPP:numeroProcesso:numeroDigitoVerificador"]', partes["digito"])
                    await page.fill('input[id="fPP:numeroProcesso:Ano"]', partes["ano"])
                    await page.fill('input[id="fPP:numeroProcesso:ramoJustica"]', partes["ramo"])
                    await page.fill('input[id="fPP:numeroProcesso:respectivoTribunal"]', partes["tribunal"])
                    await page.fill('input[id="fPP:numeroProcesso:NumeroOrgaoJustica"]', partes["orgao"])
                    
                    print(f"Campos preenchidos para o processo {proc}. Realizando busca...")
                    # Aqui você clicaria no botão de pesquisar, ex:
                    # await page.click("#fPP:searchProcessos")
                    # await page.wait_for_timeout(2000) # Aguarda um pouco entre as buscas
                else:
                    print(f"Aviso: Formato de processo inválido: {proc}")

        else:
            print(f"Erro: Não foi possível confirmar a página de consulta. URL atual: {page.url}")

        while True:
            comando = input("\nDigite 'sair' para encerrar o bot: ").strip().lower()
            if comando == 'sair':
                print("Encerrando o bot...")
                break

        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot interrompido pelo usuário.")
    except Exception as e:
        print(f"\nOcorreu um erro: {e}")

import requests
import csv
import json
import os
import sys
import time
from requests.adapters import HTTPAdapter, Retry

# --- Configurações ---
SONAR_HOST = os.environ.get("SONAR_HOST", "http://localhost:9000")

# IMPORTANTE: o token está no hardcoded no código-fonte.
# Defina a variável de ambiente antes de rodar, por exemplo (PowerShell):
#   $env:SONAR_TOKEN = "seu_token_aqui"
# ou (cmd):
#   set SONAR_TOKEN=seu_token_aqui
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")

# Se você precisa medir uma branch específica (não a principal), defina aqui.
# Deixe como None para usar a branch padrão do projeto.
BRANCH = os.environ.get("SONAR_BRANCH")  # ex: "main" ou None

DIRETORIO_SAIDA = os.environ.get("DIRETORIO_SAIDA", r"G:\trab-ia03")

ARQUIVO_CSV = os.path.join(DIRETORIO_SAIDA, "metricas_swebench.csv")
ARQUIVO_JSON = os.path.join(DIRETORIO_SAIDA, "metricas_swebench.json")
ARQUIVO_LOG_ERROS = os.path.join(DIRETORIO_SAIDA, "erros_extracao.log")

# Lista exata das métricas que serão extraídas.
# Chaves confirmadas como válidas na documentação atual do SonarQube (2026.1).
# Adicione outras chaves oficiais do SonarQube se necessário para sua dissertação.
METRICAS = [
    "complexity",            # Complexidade Ciclomática (McCabe)
    "cognitive_complexity",  # Complexidade Cognitiva
    "ncloc",                 # Linhas de Código Não-Comentadas
    "bugs",
    "vulnerabilities",
    "code_smells",
    "sqale_index",           # Dívida Técnica (em minutos)
]

TIMEOUT_SEGUNDOS = 30


def criar_sessao():
    """Cria uma sessão HTTP com autenticação Bearer e retry automático."""
    if not SONAR_TOKEN:
        print("ERRO: variável de ambiente SONAR_TOKEN não definida. Abortando.")
        sys.exit(1)

    sessao = requests.Session()
    sessao.headers.update({"Authorization": f"Bearer {SONAR_TOKEN}"})

    retries = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adaptador = HTTPAdapter(max_retries=retries)
    sessao.mount("http://", adaptador)
    sessao.mount("https://", adaptador)
    return sessao


def obter_todos_projetos(sessao):
    projetos = []
    pagina = 1
    tamanho_pagina = 100  # O limite seguro para paginação no SonarQube

    print("Mapeando projetos no SonarQube...")
    while True:
        url = f"{SONAR_HOST}/api/components/search"
        parametros = {"qualifiers": "TRK", "ps": tamanho_pagina, "p": pagina}
        try:
            resposta = sessao.get(url, params=parametros, timeout=TIMEOUT_SEGUNDOS)
        except requests.exceptions.RequestException as e:
            print(f"Falha de conexão ao buscar projetos na página {pagina}: {e}")
            break

        if resposta.status_code != 200:
            print(f"Erro ao buscar projetos na página {pagina}: {resposta.status_code} - {resposta.text}")
            break

        dados = resposta.json()
        componentes = dados.get("components", [])

        if not componentes:
            break

        for comp in componentes:
            projetos.append(comp["key"])

        pagina += 1

    print(f"Total de projetos encontrados: {len(projetos)}")
    return projetos


def exportar_metricas():
    os.makedirs(DIRETORIO_SAIDA, exist_ok=True)
    sessao = criar_sessao()

    projetos = obter_todos_projetos(sessao)
    if not projetos:
        print("Nenhum projeto encontrado para exportação.")
        return

    chaves_metricas_str = ",".join(METRICAS)
    resultados_finais = []
    erros = []

    print("Extraindo métricas individuais...")
    for index, projeto_key in enumerate(projetos, 1):
        url = f"{SONAR_HOST}/api/measures/component"
        parametros = {"component": projeto_key, "metricKeys": chaves_metricas_str}
        if BRANCH:
            parametros["branch"] = BRANCH

        try:
            resposta = sessao.get(url, params=parametros, timeout=TIMEOUT_SEGUNDOS)
        except requests.exceptions.RequestException as e:
            msg = f"[{index}/{len(projetos)}] Falha de conexão em {projeto_key}: {e}"
            print(msg)
            erros.append(msg)
            continue

        if resposta.status_code != 200:
            msg = f"[{index}/{len(projetos)}] Erro ao extrair {projeto_key}: {resposta.status_code} - {resposta.text}"
            print(msg)
            erros.append(msg)
            continue

        medidas = resposta.json().get("component", {}).get("measures", [])

        # Estrutura base da linha para este projeto
        linha_dados = {"instance_id": projeto_key}

        # Inicializa todas as métricas requisitadas como N/A para evitar buracos nos dados
        for metrica in METRICAS:
            linha_dados[metrica] = "N/A"

        # Preenche os valores encontrados
        for medida in medidas:
            linha_dados[medida["metric"]] = medida.get("value", "N/A")

        resultados_finais.append(linha_dados)
        print(f"[{index}/{len(projetos)}] Sucesso: {projeto_key}")

        # Pequena pausa para não sobrecarregar a API em análises com muitos projetos
        time.sleep(0.05)

    # Gravação no disco
    # 1. Exportação JSON
    with open(ARQUIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(resultados_finais, f, indent=4, ensure_ascii=False)

    # 2. Exportação CSV
    colunas = ["instance_id"] + METRICAS
    with open(ARQUIVO_CSV, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(resultados_finais)

    # 3. Log de erros, se houver, para rastreabilidade na dissertação
    if erros:
        with open(ARQUIVO_LOG_ERROS, "w", encoding="utf-8") as f:
            f.write("\n".join(erros))

    print(f"\nExtração concluída.")
    print(f"Projetos processados com sucesso: {len(resultados_finais)}/{len(projetos)}")
    if erros:
        print(f"Projetos com erro: {len(erros)} (ver {ARQUIVO_LOG_ERROS})")
    print(f"Arquivo CSV salvo em: {ARQUIVO_CSV}")
    print(f"Arquivo JSON salvo em: {ARQUIVO_JSON}")


if __name__ == "__main__":
    exportar_metricas()

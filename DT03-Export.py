import requests
import csv
import json
import os
import sys
import time
from requests.adapters import HTTPAdapter, Retry

# --- Configurações ---
SONAR_HOST = os.environ.get("SONAR_HOST", "http://localhost:9000")

# IMPORTANTE: o token NÃO fica hardcoded no código-fonte.
# A variável de ambiente devará ser setada antes de rodar, por exemplo (PowerShell):
#   $env:SONAR_TOKEN = "seu_token_aqui"
# ou (cmd):
#   set SONAR_TOKEN=seu_token_aqui
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")

# Medição de uma branch específica (não a principal), defina aqui.
# Deixa como None para usar a branch padrão do projeto.
BRANCH = os.environ.get("SONAR_BRANCH")  # ex: "main" ou None

DIRETORIO_SAIDA = os.environ.get("DIRETORIO_SAIDA", r"G:\trab-ia03")

ARQUIVO_CSV = os.path.join(DIRETORIO_SAIDA, "metricas_completas_swebench.csv")
ARQUIVO_JSON = os.path.join(DIRETORIO_SAIDA, "metricas_completas_swebench.json")
ARQUIVO_LOG_ERROS = os.path.join(DIRETORIO_SAIDA, "erros_extracao_completa.log")

TIMEOUT_SEGUNDOS = 30
TAMANHO_LOTE_METRICAS = 50  # Respeita limites de URL GET


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


def obter_todas_chaves_metricas(sessao):
    print("Consultando dicionário de métricas do SonarQube...")
    url = f"{SONAR_HOST}/api/metrics/search"
    try:
        resposta = sessao.get(url, params={"ps": 500}, timeout=TIMEOUT_SEGUNDOS)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Falha de conexão ao obter dicionário de métricas: {e}")

    if resposta.status_code != 200:
        raise Exception(f"Falha ao obter dicionário de métricas. Status: {resposta.status_code} - {resposta.text}")

    metricas = resposta.json().get("metrics", [])
    # Filtra apenas as métricas numéricas ou textuais que fazem sentido exportar
    chaves = [m["key"] for m in metricas if m["type"] not in ["DATA"]]
    print(f"Total de tipos de métricas mapeadas: {len(chaves)}")
    return chaves


def obter_todos_projetos(sessao):
    projetos = []
    pagina = 1
    tamanho_pagina = 100

    print("Mapeando projetos...")
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


def chunk_list(lista, tamanho):
    """Divide uma lista em sublistas menores para evitar URLs muito longas."""
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def exportar_metricas_totais():
    os.makedirs(DIRETORIO_SAIDA, exist_ok=True)
    sessao = criar_sessao()

    chaves_totais = obter_todas_chaves_metricas(sessao)
    projetos = obter_todos_projetos(sessao)

    if not projetos or not chaves_totais:
        print("Operação abortada: Projetos ou métricas não encontrados.")
        return

    resultados_finais = []
    erros = []
    print("\nExtraindo dados...")

    for index, projeto_key in enumerate(projetos, 1):
        linha_dados = {"instance_id": projeto_key}
        # Inicializa todas as chaves como N/A para evitar colunas vazias no CSV
        for chave in chaves_totais:
            linha_dados[chave] = "N/A"

        # Pede as métricas em lotes para respeitar limites de URL GET
        sucesso_projeto = True
        for lote_chaves in chunk_list(chaves_totais, TAMANHO_LOTE_METRICAS):
            chaves_str = ",".join(lote_chaves)
            url = f"{SONAR_HOST}/api/measures/component"
            parametros = {"component": projeto_key, "metricKeys": chaves_str}
            if BRANCH:
                parametros["branch"] = BRANCH

            try:
                resposta = sessao.get(url, params=parametros, timeout=TIMEOUT_SEGUNDOS)
            except requests.exceptions.RequestException as e:
                sucesso_projeto = False
                erros.append(f"[{index}/{len(projetos)}] {projeto_key}: falha de conexão - {e}")
                continue

            if resposta.status_code == 200:
                medidas = resposta.json().get("component", {}).get("measures", [])
                for medida in medidas:
                    linha_dados[medida["metric"]] = medida.get("value", "N/A")
            else:
                sucesso_projeto = False
                erros.append(
                    f"[{index}/{len(projetos)}] {projeto_key}: {resposta.status_code} - {resposta.text}"
                )

        if sucesso_projeto:
            print(f"[{index}/{len(projetos)}] Exportado: {projeto_key}")
        else:
            print(f"[{index}/{len(projetos)}] Erro parcial na extração de: {projeto_key}")

        resultados_finais.append(linha_dados)

        # Pequena pausa para não sobrecarregar a API (cada projeto já gera vários requests em lote)
        time.sleep(0.05)

    # Gravação JSON
    with open(ARQUIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(resultados_finais, f, indent=4, ensure_ascii=False)

    # Gravação CSV
    colunas = ["instance_id"] + chaves_totais
    with open(ARQUIVO_CSV, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(resultados_finais)

    # Log de erros, se houver, para rastreabilidade na dissertação
    if erros:
        with open(ARQUIVO_LOG_ERROS, "w", encoding="utf-8") as f:
            f.write("\n".join(erros))

    print(f"\nFinalizado. CSV bruto com todas as variáveis gerado em: {ARQUIVO_CSV}")
    if erros:
        print(f"Ocorreram {len(erros)} erros/lotes com falha (ver {ARQUIVO_LOG_ERROS})")


if __name__ == "__main__":
    exportar_metricas_totais()

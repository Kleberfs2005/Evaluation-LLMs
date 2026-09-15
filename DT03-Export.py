import requests
import csv
import json
import os

# --- Configurações ---
SONAR_HOST = "http://localhost:9000"
SONAR_TOKEN = "squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DIRETORIO_SAIDA = r"G:\trab-ia03"

ARQUIVO_CSV = os.path.join(DIRETORIO_SAIDA, "metricas_swebench.csv")
ARQUIVO_JSON = os.path.join(DIRETORIO_SAIDA, "metricas_swebench.json")

# Lista exata das métricas que serão extraídas. 
# Adicione outras chaves oficiais do SonarQube se necessário para sua dissertação.
METRICAS = [
    "complexity",            # Complexidade Ciclomática (McCabe)
    "cognitive_complexity",  # Complexidade Cognitiva
    "ncloc",                 # Linhas de Código Não-Comentadas
    "bugs",
    "vulnerabilities",
    "code_smells",
    "sqale_index"            # Dívida Técnica (em minutos)
]

def obter_todos_projetos():
    projetos = []
    pagina = 1
    tamanho_pagina = 100 # O limite seguro para paginação no SonarQube
    
    print("Mapeando projetos no SonarQube...")
    while True:
        url = f"{SONAR_HOST}/api/components/search?qualifiers=TRK&ps={tamanho_pagina}&p={pagina}"
        resposta = requests.get(url, auth=(SONAR_TOKEN, ''))
        
        if resposta.status_code != 200:
            print(f"Erro ao buscar projetos na página {pagina}: {resposta.status_code}")
            break
            
        dados = resposta.json()
        componentes = dados.get('components', [])
        
        if not componentes:
            break
            
        for comp in componentes:
            projetos.append(comp['key'])
            
        pagina += 1
        
    print(f"Total de projetos encontrados: {len(projetos)}")
    return projetos

def exportar_metricas():
    projetos = obter_todos_projetos()
    if not projetos:
        print("Nenhum projeto encontrado para exportação.")
        return

    chaves_metricas_str = ",".join(METRICAS)
    resultados_finais = []

    print("Extraindo métricas individuais...")
    for index, projeto_key in enumerate(projetos, 1):
        url = f"{SONAR_HOST}/api/measures/component?component={projeto_key}&metricKeys={chaves_metricas_str}"
        resposta = requests.get(url, auth=(SONAR_TOKEN, ''))
        
        if resposta.status_code != 200:
            print(f"[{index}/{len(projetos)}] Erro ao extrair {projeto_key}")
            continue

        medidas = resposta.json().get('component', {}).get('measures', [])
        
        # Estrutura base da linha para este projeto
        linha_dados = {"instance_id": projeto_key}
        
        # Inicializa todas as métricas requisitadas como N/A para evitar buracos nos dados
        for metrica in METRICAS:
            linha_dados[metrica] = "N/A"
            
        # Preenche os valores encontrados
        for medida in medidas:
            linha_dados[medida['metric']] = medida.get('value', 'N/A')
            
        resultados_finais.append(linha_dados)
        print(f"[{index}/{len(projetos)}] Sucesso: {projeto_key}")

    # Gravação no disco
    # 1. Exportação JSON
    with open(ARQUIVO_JSON, 'w', encoding='utf-8') as f:
        json.dump(resultados_finais, f, indent=4)

    # 2. Exportação CSV
    colunas = ["instance_id"] + METRICAS
    with open(ARQUIVO_CSV, 'w', newline='', encoding='utf-8') as f:
        escritor = csv.DictWriter(f, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(resultados_finais)

    print(f"\nExtração concluída com sucesso.")
    print(f"Arquivo CSV salvo em: {ARQUIVO_CSV}")
    print(f"Arquivo JSON salvo em: {ARQUIVO_JSON}")

if __name__ == "__main__":
    exportar_metricas()
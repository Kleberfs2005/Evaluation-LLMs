import json
import os
import subprocess

# --- Configurações Iniciais ---
ARQUIVO_MAPEAMENTO = "mapeamento_commits.json"
DIRETORIO_WORKSPACE = "./workspace_repos"
SONAR_HOST = "http://localhost:9000"
SONAR_TOKEN = "squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" # Substitua pelo token gerado na interface web

os.makedirs(DIRETORIO_WORKSPACE, exist_ok=True)

with open(ARQUIVO_MAPEAMENTO, 'r', encoding='utf-8') as f:
    dados_mapeamento = json.load(f)

for instance_id, info in dados_mapeamento.items():
    repo_nome = info['repo']
    base_commit = info['base_commit']
    # Converte o caminho do patch para absoluto, pois o cwd mudará durante a execução
    caminho_patch = os.path.abspath(info['caminho_patch']) 
    
    url_repositorio = f"https://github.com/{repo_nome}.git"
    dir_repositorio_local = os.path.join(DIRETORIO_WORKSPACE, instance_id)

    print(f"\n--- Processando: {instance_id} ---")

    try:
        # 1. Clonar o repositório (apenas se não existir localmente)
        if not os.path.exists(dir_repositorio_local):
            print("Clonando repositório...")
            subprocess.run(["git", "clone", url_repositorio, dir_repositorio_local], check=True)

        # 2. Resetar o repositório para evitar contaminação de execuções anteriores
        subprocess.run(["git", "-C", dir_repositorio_local, "reset", "--hard"], stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["git", "-C", dir_repositorio_local, "clean", "-fd"], stdout=subprocess.DEVNULL, check=True)
        
        # 3. Checkout para o commit exato onde o bug existia
        subprocess.run(["git", "-C", dir_repositorio_local, "checkout", base_commit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

# 4. Aplicar o patch gerado pelo modelo
        # Modificação: Usamos o Git com --recount para ignorar a matemática falha do LLM
        resultado_patch = subprocess.run(
            ["git", "-C", dir_repositorio_local, "apply", "--recount", "--whitespace=fix", caminho_patch],
            stderr=subprocess.DEVNULL
        )
        
        # Se o Git rejeitar o formato, fazemos o fallback para a ferramenta utilitária 'patch' (Padrão do SWE-bench)
        if resultado_patch.returncode != 0:
            try:
                # O parâmetro --fuzz=3 permite margem de erro estrutural nas linhas de contexto
                resultado_patch = subprocess.run(
                    ["patch", "-p1", "--fuzz=3", "-i", caminho_patch],
                    cwd=dir_repositorio_local,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                print(f"[{instance_id}] ALERTA: Ferramenta 'patch' não encontrada no Windows.")
        
        if resultado_patch.returncode != 0:
            print(f"[ERRO] O patch não pôde ser aplicado em {instance_id}. Estrutura irremediavelmente corrompida pelo LLM.")
            continue
        # 5. Executar o SonarScanner
        print("Enviando para o SonarQube...")
        comando_sonar = [
            "sonar-scanner.bat",  # <- Adicionado .bat aqui
            f"-Dsonar.projectKey={instance_id}",
            f"-Dsonar.projectName={instance_id}",
            "-Dsonar.sources=.",
            # Exclui pastas de testes e arquivos irrelevantes da análise de complexidade
            "-Dsonar.exclusions=**/tests/**,**/test_*.py,**/*_test.py", 
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.login={SONAR_TOKEN}",
            # Desativa a verificação de SCM para evitar conflitos de blame em commits detached
            "-Dsonar.scm.disabled=true" 
        ]
        
        subprocess.run(comando_sonar, cwd=dir_repositorio_local, check=True)

    except subprocess.CalledProcessError as e:
        print(f"[ERRO GRAVE] Falha na execução de comandos Git ou Sonar em {instance_id}. Detalhes: {e}")
        continue
import json
import os
import re
import platform
import subprocess

# --- Configurações Iniciais ---
ARQUIVO_MAPEAMENTO = "mapeamento_commits.json"
DIRETORIO_WORKSPACE = "./workspace_repos"          # cópias de trabalho, uma por instance_id
DIRETORIO_CACHE_MIRRORS = "./cache_repos_mirror"   # espelhos (--mirror), um por repositório único
ARQUIVO_CHECKPOINT = "progresso_sonar.json"        # registra instâncias já concluídas
SONAR_HOST = "http://localhost:9000"

# O token ficou hardcoded no código fonte.
# Defina a variável de ambiente antes de rodar o script, ex.:
#   export SONAR_TOKEN="squ_..."      (Linux/macOS)
#   set SONAR_TOKEN=squ_...           (Windows)
SONAR_TOKEN = os.environ.get("SONAR_TOKEN")
if not SONAR_TOKEN:
    raise EnvironmentError(
        "Variável de ambiente SONAR_TOKEN não definida. "
        "Gere um User Token em My Account > Security no SonarQube e exporte-o antes de executar o script."
    )

# Executável do scanner: portável entre Windows e Linux/macOS
SONAR_SCANNER_BIN = "sonar-scanner.bat" if platform.system() == "Windows" else "sonar-scanner"

os.makedirs(DIRETORIO_WORKSPACE, exist_ok=True)
os.makedirs(DIRETORIO_CACHE_MIRRORS, exist_ok=True)


# --- Checkpoint / retomada ---------------------------------------------------

def carregar_checkpoint():
    """Carrega o registro de instâncias já concluídas com sucesso em execuções anteriores."""
    if os.path.exists(ARQUIVO_CHECKPOINT):
        with open(ARQUIVO_CHECKPOINT, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def salvar_progresso_instancia(checkpoint, instance_id, status):
    """
    Grava o status IMEDIATAMENTE após cada instância (não só ao final do loop),
    para que uma interrupção do script (queda de energia, Ctrl+C, crash da JVM)
    não custe reprocessar tudo desde o início.
    """
    checkpoint[instance_id] = status
    with open(ARQUIVO_CHECKPOINT, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f, indent=2, ensure_ascii=False)


# --- Cache de repositório compartilhado (elimina reclone redundante) --------

def obter_mirror_local(repo_nome):
    """
    Garante que exista um clone --mirror LOCAL do repositório (histórico completo,
    sem working tree) e o retorna. Esse clone é feito da rede apenas UMA VEZ por
    repositório único (ex.: 'django/django'), mesmo que ele apareça em dezenas
    de instâncias do dataset com base_commits diferentes.
    """
    nome_seguro = repo_nome.replace("/", "__")
    caminho_mirror = os.path.join(DIRETORIO_CACHE_MIRRORS, f"{nome_seguro}.git")

    if not os.path.exists(caminho_mirror):
        url_repositorio = f"https://github.com/{repo_nome}.git"
        print(f"[cache] Repositório '{repo_nome}' ainda não está em cache. Clonando mirror (uma única vez)...")
        subprocess.run(["git", "clone", "--mirror", url_repositorio, caminho_mirror], check=True)
    # Se o mirror já existe, ele é reaproveitado sem nenhuma chamada de rede.

    return caminho_mirror


def preparar_worktree_da_instancia(caminho_mirror, dir_repositorio_local, base_commit):
    """
    Materializa a cópia de trabalho de uma instância a partir do mirror LOCAL
    (operação de disco, não de rede) e faz o checkout do base_commit.
    Se a cópia de trabalho já existir (de uma execução anterior interrompida),
    ela é reaproveitada e apenas resetada/limpa.
    """
    if not os.path.exists(dir_repositorio_local):
        # Clone local a partir do mirror: rápido, sem tráfego de rede.
        subprocess.run(["git", "clone", caminho_mirror, dir_repositorio_local], check=True)

    # Reseta para evitar contaminação de execuções anteriores
    subprocess.run(["git", "-C", dir_repositorio_local, "reset", "--hard"], stdout=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "-C", dir_repositorio_local, "clean", "-fd"], stdout=subprocess.DEVNULL, check=True)

    # Checkout para o commit exato onde o bug existia
    subprocess.run(
        ["git", "-C", dir_repositorio_local, "checkout", base_commit],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
    )


# --- Extração dos arquivos modificados (para sonar.inclusions) --------------

def extrair_arquivos_modificados(caminho_patch):
    """
    Extrai, a partir do patch em Unified Diff, os caminhos dos arquivos
    efetivamente modificados (linhas '+++ b/<arquivo>').
    Esses caminhos serão usados para restringir o escopo da análise via
    sonar.inclusions, conforme a metodologia definida na Etapa 03
    (Analysis Isolation).
    """
    arquivos = set()
    with open(caminho_patch, 'r', encoding='utf-8', errors='ignore') as f:
        for linha in f:
            m = re.match(r'^\+\+\+ b/(.+)$', linha.rstrip('\n'))
            if m:
                arquivos.add(m.group(1).strip())
    return sorted(arquivos)


# --- Execução principal -------------------------------------------------------

with open(ARQUIVO_MAPEAMENTO, 'r', encoding='utf-8') as f:
    dados_mapeamento = json.load(f)

checkpoint = carregar_checkpoint()
ja_concluidos = sum(1 for status in checkpoint.values() if status == "concluido")
print(f"Checkpoint carregado: {ja_concluidos} instância(s) já concluída(s) em execuções anteriores.")

for instance_id, info in dados_mapeamento.items():

    # Retomada: pula instâncias já processadas com sucesso, sem tocar em rede,
    # disco (além da leitura do checkpoint) ou JVM.
    if checkpoint.get(instance_id) == "concluido":
        print(f"[pulando] {instance_id} já concluído anteriormente.")
        continue

    repo_nome = info['repo']
    base_commit = info['base_commit']
    # Converte o caminho do patch para absoluto, pois o cwd mudará durante a execução
    caminho_patch = os.path.abspath(info['caminho_patch'])

    dir_repositorio_local = os.path.join(DIRETORIO_WORKSPACE, instance_id)

    print(f"\n--- Processando: {instance_id} ---")

    try:
        # 1. Obter (ou reaproveitar) o mirror local do repositório -- só baixa da rede
        #    na primeira vez que esse repositório aparece na fila de instâncias.
        caminho_mirror = obter_mirror_local(repo_nome)

        # 2. Materializar a cópia de trabalho a partir do mirror local e fazer checkout
        #    do base_commit (operação local, sem rede).
        preparar_worktree_da_instancia(caminho_mirror, dir_repositorio_local, base_commit)

        # 3. Aplicar o patch gerado pelo modelo
        # Usamos o Git com --recount para tolerar contagens de linha inconsistentes no diff
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
                print(f"[{instance_id}] ALERTA: Ferramenta 'patch' não encontrada no sistema.")

        if resultado_patch.returncode != 0:
            print(f"[ERRO] O patch não pôde ser aplicado em {instance_id}. Estrutura irremediavelmente corrompida pelo LLM.")
            salvar_progresso_instancia(checkpoint, instance_id, "erro_patch")
            continue

        # 4. Extrair a lista de arquivos modificados pelo patch para restringir
        #    o escopo da análise (Analysis Isolation, conforme Etapa 03).
        arquivos_modificados = extrair_arquivos_modificados(caminho_patch)
        if not arquivos_modificados:
            print(f"[AVISO] Nenhum arquivo identificado no patch de {instance_id}. Pulando instância para evitar análise não isolada.")
            salvar_progresso_instancia(checkpoint, instance_id, "erro_sem_arquivos")
            continue

        inclusions_str = ",".join(arquivos_modificados)

        # 5. Executar o SonarScanner
        print("Enviando para o SonarQube...")
        comando_sonar = [
            SONAR_SCANNER_BIN,
            f"-Dsonar.projectKey={instance_id}",
            f"-Dsonar.projectName={instance_id}",
            "-Dsonar.sources=.",
            # Restringe a análise exclusivamente aos arquivos tocados pelo patch da LLM
            f"-Dsonar.inclusions={inclusions_str}",
            # Exclui pastas de testes e arquivos irrelevantes da análise de complexidade,
            # mesmo que estejam entre os arquivos modificados pelo patch
            "-Dsonar.exclusions=**/tests/**,**/test_*.py,**/*_test.py",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.login={SONAR_TOKEN}",
            # Desativa a verificação de SCM para evitar conflitos de blame em commits detached
            "-Dsonar.scm.disabled=true"
        ]

        subprocess.run(comando_sonar, cwd=dir_repositorio_local, check=True)

        # Só marca como concluído depois que o SonarScanner retornou com sucesso.
        salvar_progresso_instancia(checkpoint, instance_id, "concluido")

    except subprocess.CalledProcessError as e:
        print(f"[ERRO GRAVE] Falha na execução de comandos Git ou Sonar em {instance_id}. Detalhes: {e}")
        salvar_progresso_instancia(checkpoint, instance_id, "erro_execucao")
        continue

print(f"\nProcessamento finalizado. Verifique '{ARQUIVO_CHECKPOINT}' para o status detalhado de cada instância.")

"""
DT02-Sonar-corrigido-oficial.py

Pipeline "do zero" para a Etapa 04 (Orquestração e Análise Estática com SonarQube)
da metodologia de análise triaxial de código gerado por LLM.

Pré-requisitos:
  1. SonarQube rodando localmente (ou acessível via SONAR_HOST) com um banco de
     dados persistente configurado (H2 embutido não é recomendado para 500+
     análises; prefira PostgreSQL).
  2. sonar-scanner instalado e disponível no PATH.
  3. Um User Token do SonarQube (My Account > Security) exportado na variável
     de ambiente SONAR_TOKEN antes de rodar este script:
       Linux/macOS:  export SONAR_TOKEN="squ_..."
       Windows PS:   $env:SONAR_TOKEN = "squ_..."
  4. mapeamento_commits.json já gerado (ver 2mapear_base_commit.py, Etapa 03),
     na mesma pasta deste script (ou informe o caminho via --mapeamento).

Uso:
  python DT02-Sonar.py                  # processa tudo
  python DT02-Sonar.py --limit 10       # smoke test com 10 instâncias
  python DT02-Sonar.py --retomar        # (padrão) pula concluídos
"""

import argparse
import json
import os
import platform
import re
import subprocess

# --- Configurações Iniciais ---
ARQUIVO_MAPEAMENTO = "mapeamento_commits.json"
DIRETORIO_WORKSPACE = "./workspace_repos"          # cópias de trabalho, uma por instance_id
DIRETORIO_CACHE_MIRRORS = "./cache_repos_mirror"   # espelhos (--mirror), um por repositório único
ARQUIVO_CHECKPOINT = "progresso_sonar.json"        # registra instâncias já concluídas
SONAR_HOST = "http://localhost:9000"

# Pasta onde está o mapeamento_commits.json -- usada como âncora para resolver
# caminhos relativos de patch, independente de qual pasta o script é chamado.
BASE_DIR = os.path.dirname(os.path.abspath(ARQUIVO_MAPEAMENTO))

# O token nunca deve ficar hardcoded no código-fonte.
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


# --- Resolução robusta do caminho do patch -----------------------------------

def resolver_caminho_patch(caminho_patch):
    """
    Tenta resolver o caminho do .patch de várias formas (absoluto, relativo à
    pasta do mapeamento_commits.json, relativo ao cwd atual), nessa ordem, e
    retorna o primeiro que existir de fato. Evita que o script funcione "por
    acaso" apenas quando chamado de uma pasta específica.
    """
    candidatos = [
        caminho_patch,
        os.path.join(BASE_DIR, caminho_patch),
        os.path.abspath(caminho_patch),
    ]
    for c in candidatos:
        c_norm = os.path.normpath(c)
        if os.path.isfile(c_norm):
            return c_norm
    return None


# --- Extração dos arquivos modificados (para sonar.sources) -----------------

def extrair_arquivos_modificados(caminho_patch_bruto):
    """
    Extrai, a partir do patch em Unified Diff, os caminhos dos arquivos
    efetivamente modificados (linhas '+++ b/<arquivo>', com fallback para o
    cabeçalho 'diff --git a/x b/x' caso o primeiro padrão não bata com nada --
    útil se algum outro baseline gerar patches com formatação ligeiramente
    diferente). Esses caminhos são usados diretamente em sonar.sources, para
    isolar a análise aos arquivos tocados pelo LLM (Etapa 03) SEM varrer a
    árvore de diretórios inteira do repositório -- é essa varredura completa
    que torna sonar.sources="." + sonar.inclusions=... muito mais lento na
    prática, mesmo produzindo métricas corretas.
    """
    caminho_resolvido = resolver_caminho_patch(caminho_patch_bruto)
    if caminho_resolvido is None:
        return None  # None = caminho do .patch não encontrado (erro de configuração)

    with open(caminho_resolvido, 'r', encoding='utf-8', errors='ignore') as f:
        conteudo = f.read()

    arquivos = []
    padrao_plus = re.compile(r'^\+\+\+ (?:b/)?(\S+)', re.MULTILINE)
    for m in padrao_plus.finditer(conteudo):
        caminho = m.group(1).strip()
        if caminho and caminho != "/dev/null":
            arquivos.append(caminho)

    if not arquivos:
        padrao_diff = re.compile(r'^diff --git a/(\S+) b/(\S+)', re.MULTILINE)
        for m in padrao_diff.finditer(conteudo):
            arquivos.append(m.group(2).strip())

    vistos, unicos = set(), []
    for a in arquivos:
        if a not in vistos:
            vistos.add(a)
            unicos.append(a)
    return unicos  # lista vazia = patch lido, mas nenhum arquivo identificado


# --- Execução principal -------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Etapa 04: orquestração e análise estática via SonarQube (pipeline do zero).")
    ap.add_argument("--limit", type=int, default=None,
                     help="Processar só as N primeiras instâncias (recomendado para um smoke test antes do lote completo).")
    ap.add_argument("--retomar", action="store_true", default=True,
                     help="Pular instâncias já marcadas como 'concluido' no checkpoint (padrão: ativado).")
    args = ap.parse_args()

    with open(ARQUIVO_MAPEAMENTO, 'r', encoding='utf-8') as f:
        dados_mapeamento = json.load(f)

    itens = list(dados_mapeamento.items())
    if args.limit:
        itens = itens[:args.limit]

    checkpoint = carregar_checkpoint()
    ja_concluidos = sum(1 for status in checkpoint.values() if status == "concluido")
    print(f"Checkpoint carregado: {ja_concluidos} instância(s) já concluída(s) em execuções anteriores.")
    print(f"Instâncias nesta execução: {len(itens)}")

    for instance_id, info in itens:

        if args.retomar and checkpoint.get(instance_id) == "concluido":
            print(f"[pulando] {instance_id} já concluído anteriormente.")
            continue

        repo_nome = info['repo']
        base_commit = info['base_commit']
        dir_repositorio_local = os.path.join(DIRETORIO_WORKSPACE, instance_id)

        print(f"\n--- Processando: {instance_id} ---")

        try:
            # 1. Obter (ou reaproveitar) o mirror local do repositório -- só baixa da
            #    rede na primeira vez que esse repositório aparece na fila.
            caminho_mirror = obter_mirror_local(repo_nome)

            # 2. Materializar a cópia de trabalho a partir do mirror local e fazer
            #    checkout do base_commit (operação local, sem rede).
            preparar_worktree_da_instancia(caminho_mirror, dir_repositorio_local, base_commit)

            # 3. Aplicar o patch gerado pelo modelo.
            arquivos_modificados = extrair_arquivos_modificados(info['caminho_patch'])
            if arquivos_modificados is None:
                print(f"[ERRO] Arquivo .patch de {instance_id} não foi localizado em disco "
                      f"(caminho informado: {info['caminho_patch']}). Verifique o mapeamento_commits.json.")
                salvar_progresso_instancia(checkpoint, instance_id, "erro_patch_nao_encontrado")
                continue

            caminho_patch_resolvido = resolver_caminho_patch(info['caminho_patch'])

            # Usamos o Git com --recount para tolerar contagens de linha inconsistentes no diff
            resultado_patch = subprocess.run(
                ["git", "-C", dir_repositorio_local, "apply", "--recount", "--whitespace=fix", caminho_patch_resolvido],
                stderr=subprocess.DEVNULL
            )

            # Se o Git rejeitar o formato, fazemos o fallback para a ferramenta utilitária 'patch' (Padrão do SWE-bench)
            if resultado_patch.returncode != 0:
                try:
                    resultado_patch = subprocess.run(
                        ["patch", "-p1", "--fuzz=3", "-i", caminho_patch_resolvido],
                        cwd=dir_repositorio_local,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except FileNotFoundError:
                    print(f"[{instance_id}] ALERTA: Ferramenta 'patch' não encontrada no sistema.")

            if resultado_patch.returncode != 0:
                print(f"[ERRO] O patch não pôde ser aplicado em {instance_id}. "
                      f"Estrutura irremediavelmente corrompida pelo LLM (patch referencia arquivo/contexto inexistente).")
                salvar_progresso_instancia(checkpoint, instance_id, "erro_patch")
                continue

            if not arquivos_modificados:
                print(f"[AVISO] Nenhum arquivo identificado no patch de {instance_id}. "
                      f"Pulando instância para evitar análise não isolada.")
                salvar_progresso_instancia(checkpoint, instance_id, "erro_sem_arquivos")
                continue

            # 4. Executar o SonarScanner com sonar.sources apontando DIRETAMENTE para
            #    os arquivos tocados pelo patch -- isso implementa o "Analysis
            #    Isolation" da Etapa 03 sem varrer a árvore de diretórios inteira do
            #    repositório, o que é o que torna a análise rápida (segundos, não
            #    minutos, por instância).
            fontes = ",".join(arquivos_modificados)

            print("Enviando para o SonarQube...")
            comando_sonar = [
                SONAR_SCANNER_BIN,
                f"-Dsonar.projectKey={instance_id}",
                f"-Dsonar.projectName={instance_id}",
                f"-Dsonar.sources={fontes}",
                # Rede de segurança: caso algum arquivo modificado seja um arquivo de
                # teste, ele ainda é excluído da análise de complexidade.
                "-Dsonar.exclusions=**/tests/**,**/test_*.py,**/*_test.py",
                f"-Dsonar.host.url={SONAR_HOST}",
                f"-Dsonar.login={SONAR_TOKEN}",
                # Desativa a verificação de SCM para evitar conflitos de blame em commits detached
                "-Dsonar.scm.disabled=true",
            ]

            subprocess.run(comando_sonar, cwd=dir_repositorio_local, check=True)

            # Só marca como concluído depois que o SonarScanner retornou com sucesso.
            salvar_progresso_instancia(checkpoint, instance_id, "concluido")

        except subprocess.CalledProcessError as e:
            print(f"[ERRO GRAVE] Falha na execução de comandos Git ou Sonar em {instance_id}. Detalhes: {e}")
            salvar_progresso_instancia(checkpoint, instance_id, "erro_execucao")
            continue

    print(f"\nProcessamento finalizado. Verifique '{ARQUIVO_CHECKPOINT}' para o status detalhado de cada instância.")


if __name__ == "__main__":
    main()

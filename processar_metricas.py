import json
import os
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError
from dotenv import load_dotenv
from tqdm import tqdm

# Carrega variáveis do arquivo .env automaticamente
load_dotenv()

# 1. Configuração da API
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("A variável de ambiente GEMINI_API_KEY não está configurada.")

client = genai.Client(api_key=API_KEY)

# 2. Definição do Prompt de Sistema
SYSTEM_INSTRUCTION = """
Você atua como um avaliador determinístico de engenharia de software. Sua tarefa é ler um texto descritivo (raciocínio analítico) referente à avaliação de um patch gerado por IA e extrair três métricas semânticas ordinais de qualidade.

Regras de Inferência Restrita:
1. Baseie-se exclusivamente no texto fornecido.
2. Se o texto for omisso sobre um critério específico, utilize obrigatoriamente o valor padrão (Fallback).

Métricas:
1. M_profundidade (1 a 3):
   - 1 (Trata sintoma/contorno)
   - 2 (Fallback - Resolve caso isolado descritivamente)
   - 3 (Resolve causa-raiz)
2. M_integridade (1 a 3):
   - 1 (Regressões, quebra de contratos/invariantes explícitas)
   - 2 (Degradação aceitável/Neutra - código redundante)
   - 3 (Fallback - Nenhuma violação mencionada)
3. M_defesa (1 a 3):
   - 1 (Falha em edge cases explícitos)
   - 2 (Fallback - Trata apenas o que estava na issue original)
   - 3 (Cobertura proativa de casos não citados)

O formato de saída deve ser ESTRITAMENTE um JSON com as chaves:
"M_profundidade" (int), "just_profundidade" (string, max 15 palavras),
"M_integridade" (int), "just_integridade" (string, max 15 palavras),
"M_defesa" (int), "just_defesa" (string, max 15 palavras).
"""

generation_config = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    temperature=0.0,
    response_mime_type="application/json"
)

# Valores de fallback centralizados (usados tanto no prompt quanto no código,
# garantindo consistência caso a extração falhe total ou parcialmente)
FALLBACK_METRICS = {
    "M_profundidade": 2, "just_profundidade": "Erro de extração",
    "M_integridade": 3, "just_integridade": "Erro de extração",
    "M_defesa": 2, "just_defesa": "Erro de extração"
}

REQUIRED_KEYS = ["M_profundidade", "just_profundidade",
                  "M_integridade", "just_integridade",
                  "M_defesa", "just_defesa"]


def calcular_m_coerencia(resolve_issue, score_confianca):
    if resolve_issue is True and score_confianca <= 2:
        return 0
    return 1


def validar_e_completar_metrics(llm_metrics):
    """Garante que todas as chaves esperadas existam e M_* sejam int 1-3.
    Chaves ausentes ou inválidas recebem o valor de fallback correspondente,
    ao invés de propagar um dicionário incompleto silenciosamente."""
    metrics_validadas = dict(llm_metrics) if isinstance(llm_metrics, dict) else {}
    teve_problema = False

    for chave in REQUIRED_KEYS:
        if chave not in metrics_validadas:
            metrics_validadas[chave] = FALLBACK_METRICS[chave]
            teve_problema = True

    for chave_m in ("M_profundidade", "M_integridade", "M_defesa"):
        valor = metrics_validadas.get(chave_m)
        if not isinstance(valor, int) or valor not in (1, 2, 3):
            metrics_validadas[chave_m] = FALLBACK_METRICS[chave_m]
            teve_problema = True

    return metrics_validadas, teve_problema


def chamar_llm_com_retry(raciocinio, instance_id, max_retries=3):
    """Tenta obter a extração do LLM. Retorna (llm_response_text, sucesso).
    Diferencia falhas de rate limit (retry com backoff) de outras falhas
    transitórias (também retentadas, com backoff menor), sem deixar a
    instância sem nenhum registro de saída."""
    for tentativa in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Raciocínio Analítico:\n{raciocinio}",
                config=generation_config
            )
            return response.text, True

        except APIError as e:
            if "429" in str(e) or "quota" in str(e).lower():
                wait_time = 2 ** tentativa * 2
                print(f"  [Aviso] Rate limit atingido. Aguardando {wait_time}s "
                      f"(Tentativa {tentativa + 1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                wait_time = 2 * (tentativa + 1)
                print(f"  [Erro] Falha na API para {instance_id}: {e}. "
                      f"Retentando em {wait_time}s (Tentativa {tentativa + 1}/{max_retries})...")
                time.sleep(wait_time)

        except Exception as e:
            # Cobre falhas de rede/timeout etc., que não são APIError e
            # antes escapavam do loop derrubando o registro inteiro.
            wait_time = 2 * (tentativa + 1)
            print(f"  [Erro] Falha inesperada para {instance_id}: {e}. "
                  f"Retentando em {wait_time}s (Tentativa {tentativa + 1}/{max_retries})...")
            time.sleep(wait_time)

    return None, False


def processar_arquivo(input_filepath, output_filepath):
    with open(input_filepath, 'r', encoding='utf-8') as infile, \
         open(output_filepath, 'w', encoding='utf-8') as outfile:

        linhas = infile.readlines()
        total_linhas = len(linhas)
        print(f"Iniciando processamento de {total_linhas} registros com google-genai...")

        for index, linha in enumerate(linhas):
            instance_id = f"unknown_{index}"
            try:
                dados_originais = json.loads(linha.strip())
                instance_id = dados_originais.get("instance_id", instance_id)
                avaliacao = dados_originais.get("evaluation", {})

                raciocinio = avaliacao.get("raciocinio_analitico", "")
                resolve_issue = avaliacao.get("resolve_issue", False)
                score_confianca = avaliacao.get("score_confianca", 3)

                print(f"[{index + 1}/{total_linhas}] Processando: {instance_id}")

                llm_response_text, sucesso_api = chamar_llm_com_retry(raciocinio, instance_id)

                if sucesso_api:
                    try:
                        llm_metrics_raw = json.loads(llm_response_text)
                    except json.JSONDecodeError:
                        print(f"  [Erro crítico] LLM não retornou JSON válido para {instance_id}. Usando fallback.")
                        llm_metrics_raw = {}
                else:
                    print(f"  [Erro crítico] Todas as tentativas falharam para {instance_id}. Usando fallback.")
                    llm_metrics_raw = {}

                llm_metrics, extraction_error = validar_e_completar_metrics(llm_metrics_raw)

                m_coerencia = calcular_m_coerencia(resolve_issue, score_confianca)

                registro_final = {
                    "instance_id": instance_id,
                    "LLM_Judge_Metrics": llm_metrics,
                    "Metadata_Metrics": {
                        "resolve_issue_original": resolve_issue,
                        "score_confianca_original": score_confianca,
                        "M_coerencia": m_coerencia
                    },
                    # Flag para auditoria por amostragem: permite localizar
                    # rapidamente registros que usaram valores de fallback.
                    "extraction_error": extraction_error
                }

                outfile.write(json.dumps(registro_final, ensure_ascii=False) + '\n')
                time.sleep(1.5)

            except Exception as loop_error:
                # Mesmo diante de um erro inesperado ao processar a linha
                # (ex.: JSON de entrada malformado), ainda escrevemos um
                # registro de fallback para preservar a contagem de 500
                # instâncias e deixar rastro auditável do problema.
                print(f"Erro ao processar a linha {index} ({instance_id}): {loop_error}")
                registro_erro = {
                    "instance_id": instance_id,
                    "LLM_Judge_Metrics": dict(FALLBACK_METRICS),
                    "Metadata_Metrics": {
                        "resolve_issue_original": None,
                        "score_confianca_original": None,
                        "M_coerencia": None
                    },
                    "extraction_error": True,
                    "processing_error": str(loop_error)
                }
                outfile.write(json.dumps(registro_erro, ensure_ascii=False) + '\n')


if __name__ == "__main__":
    ARQUIVO_ENTRADA = "evaluation_results.jsonl"
    ARQUIVO_SAIDA = "metricas_estruturais_extraidas.jsonl"

    if not os.path.exists(ARQUIVO_ENTRADA):
        print(f"Arquivo '{ARQUIVO_ENTRADA}' não encontrado no diretório atual.")
    else:
        processar_arquivo(ARQUIVO_ENTRADA, ARQUIVO_SAIDA)

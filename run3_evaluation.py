import os
import json
import logging
from dotenv import load_dotenv
from json import JSONDecodeError
import time 
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configuração de log para monitorar o progresso
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

# 1. Configuração da API com o novo SDK
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("A variável de ambiente GEMINI_API_KEY não foi definida. Configure-a no terminal.")

# Instancia o cliente (thread-safe, recomendado inicializar uma vez)
client = genai.Client(api_key=API_KEY)

# Caminhos absolutos baseados na localização do script (correção #1)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configurações de segurança relaxadas para avaliar código
safety_settings = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

# 2. Definição da função de chamada com tratamento de erros (Rate Limit / Internal Errors)
# Correção #4: retry também captura JSONDecodeError para respostas malformadas transitórias
@retry(
    retry=retry_if_exception_type(Exception), # Captura qualquer falha para tentar de novo
    wait=wait_exponential(multiplier=2, min=15, max=120),  
    stop=stop_after_attempt(5),
    reraise=True
)
def call_llm_judge(system_prompt: str, user_prompt: str) -> dict:
    """Faz a chamada à API garantindo a saída estruturada em JSON."""

    response = client.models.generate_content(
        model='gemini-2.5-flash',  # MODELO DE GEMINI
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,  # Determinístico
            response_mime_type="application/json",  # Força saída JSON
            safety_settings=safety_settings
        )
    )

    # Correção #3: guarda contra resposta vazia ou None
    if not response.text:
        raise APIError("Resposta vazia ou bloqueada pela API.")

    # Faz o parse da string JSON retornada pelo modelo
    return json.loads(response.text)


# 3. Função principal de orquestração
def main():
    # Correção #1: caminhos absolutos para funcionar independente do cwd
    input_file = os.path.join(BASE_DIR, 'evaluation_payload.jsonl')
    output_file = os.path.join(BASE_DIR, 'evaluation_results.jsonl')

    # Conta o total de linhas para acompanhamento
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            total_instances = sum(1 for line in f if line.strip())
    except FileNotFoundError:
        logging.error(f"Arquivo {input_file} não encontrado. Certifique-se de que ele está no diretório correto.")
        return

    # Correção #2: separa instâncias com sucesso das com erro para permitir reprocessamento
    processed_ids = set()   # instâncias concluídas com sucesso — não serão reprocessadas
    error_ids = set()       # instâncias com erro — serão reprocessadas normalmente

    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                if 'error' in record:
                    error_ids.add(record['instance_id'])
                else:
                    processed_ids.add(record['instance_id'])

        logging.info(
            f"Retomando: {len(processed_ids)} instâncias já avaliadas com sucesso, "
            f"{len(error_ids)} com erro (serão reprocessadas)."
        )

    logging.info(f"Iniciando avaliação de {total_instances} instâncias...")

    # Correção #5: contador separado para refletir o progresso real
    skipped = 0
    processed_count = 0

    with open(input_file, 'r', encoding='utf-8') as infile, \
         open(output_file, 'a', encoding='utf-8') as outfile:

        for line in infile:
            if not line.strip():
                continue

            payload = json.loads(line)
            instance_id = payload['instance_id']

            # Pula apenas os que já foram processados com sucesso
            if instance_id in processed_ids:
                skipped += 1
                continue

            processed_count += 1
            remaining = total_instances - skipped
            logging.info(f"Processando [{processed_count}/{remaining}]: {instance_id}")

            try:
                # Dispara a requisição
                evaluation_result = call_llm_judge(
                    system_prompt=payload['system_prompt'],
                    user_prompt=payload['user_prompt']
                )

                # Monta o registro final
                final_record = {
                    "instance_id": instance_id,
                    "evaluation": evaluation_result
                }

                # Salva imediatamente no disco
                outfile.write(json.dumps(final_record, ensure_ascii=False) + '\n')
                outfile.flush()

                time.sleep(3) # pausa de 3 segundos entre chamadas para evitar rate limit

            except Exception as e:
                logging.error(f"Falha crítica na instância {instance_id}: {e}")
                # Registra o erro no arquivo para rastreamento
                error_record = {
                    "instance_id": instance_id,
                    "error": str(e)
                }
                outfile.write(json.dumps(error_record, ensure_ascii=False) + '\n')
                outfile.flush()

    logging.info("Avaliação concluída.")


if __name__ == "__main__":
    main()

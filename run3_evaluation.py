import os
import json
import logging
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configuração de log para monitorar o progresso
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 1. Configuração da API com o novo SDK
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("A variável de ambiente GEMINI_API_KEY não foi definida. Configure-a no terminal.")

# Instancia o cliente (thread-safe, recomendado inicializar uma vez)
client = genai.Client(api_key=API_KEY)

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
@retry(
    retry=retry_if_exception_type(APIError),
    wait=wait_exponential(multiplier=2, min=10, max=60), # Espera de 10s até 60s entre falhas
    stop=stop_after_attempt(5)
)
def call_llm_judge(system_prompt: str, user_prompt: str) -> dict:
    """Faz a chamada à API garantindo a saída estruturada em JSON."""
    
    response = client.models.generate_content(
        model='gemini-1.5-pro-latest',
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0, # Determinístico
            response_mime_type="application/json", # Força saída JSON
            safety_settings=safety_settings
        )
    )
    
    # Faz o parse da string JSON retornada pelo modelo
    return json.loads(response.text)

# 3. Função principal de orquestração
def main():
    input_file = 'evaluation_payload.jsonl'
    output_file = 'evaluation_results.jsonl'
    
    # Conta o total de linhas para acompanhamento
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            total_instances = sum(1 for _ in f if _.strip())
    except FileNotFoundError:
        logging.error(f"Arquivo {input_file} não encontrado. Certifique-se de que ele está no diretório correto.")
        return

    processed_ids = set()
    
    # Verifica progresso anterior para retomar em caso de interrupção abrupta (Resiliência)
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    processed_ids.add(json.loads(line)['instance_id'])
        logging.info(f"Retomando: {len(processed_ids)} instâncias já avaliadas.")

    logging.info(f"Iniciando avaliação de {total_instances} instâncias...")

    with open(input_file, 'r', encoding='utf-8') as infile, \
         open(output_file, 'a', encoding='utf-8') as outfile:
        
        for i, line in enumerate(infile):
            if not line.strip():
                continue
                
            payload = json.loads(line)
            instance_id = payload['instance_id']
            
            if instance_id in processed_ids:
                continue
                
            logging.info(f"Processando [{i+1}/{total_instances}]: {instance_id}")
            
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
                
            except Exception as e:
                logging.error(f"Falha crítica na instância {instance_id}: {e}")
                # Registra o erro no arquivo para não perder o rastreamento
                error_record = {
                    "instance_id": instance_id,
                    "error": str(e)
                }
                outfile.write(json.dumps(error_record) + '\n')
                outfile.flush()

    logging.info("Avaliação concluída.")

if __name__ == "__main__":
    main()
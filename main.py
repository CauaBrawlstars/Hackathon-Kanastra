

#vitor

from fastapi import FastAPI, File, UploadFile, HTTPException
from app.config import settings
import csv
import codecs

app = FastAPI(title="API Antecipe - Hackathon")

@app.get("/saude")
def health_check():
    return {"status": "online", "data_base": settings.data_base}

@app.post("/validar-lote")
def validar_lote(arquivo: UploadFile = File(...)):
    # Validação básica de extensão
    if not arquivo.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="O arquivo deve ser um CSV.")

    # Lendo o arquivo em memória com decodificação UTF-8
    try:
        leitor = csv.DictReader(
            codecs.iterdecode(arquivo.file, 'utf-8'),
            delimiter=';'
        )
        
        # Transformamos as linhas numa lista de dicionários
        notas = list(leitor)
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao ler o CSV: {str(e)}")

    # O código do seu colega será chamado aqui no futuro.
    # Por enquanto, vamos apenas confirmar que lemos tudo certo.
    
    return {
        "mensagem": "Arquivo processado pela API",
        "quantidade_lida": len(notas),
        "exemplo_primeira_nota": notas[0] if notas else None
    }
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

app = FastAPI(title="Notificações")
_notificacoes = []
_processados = set()


@app.exception_handler(RequestValidationError)
def _erro_validacao(_: Request, e: RequestValidationError):
    return JSONResponse({"erro": "DADOS_INVALIDOS", "mensagem": "dados inválidos"}, status_code=422)


class Evento(BaseModel):
    id: str
    tipo: str
    ocorrido_em: str
    dados: dict


@app.post("/eventos")
def receber(evento: Evento):
    if evento.id in _processados or evento.tipo not in ("envio.criado", "envio.status_atualizado"):
        return {"processado": False}
    d = evento.dados
    tipo = "CRIADO" if evento.tipo == "envio.criado" else d["status"]
    if tipo in MENSAGENS:
        _notificacoes.append({
            "id": f"NOT-{len(_notificacoes) + 1:06d}",
            "email": d["destinatario_email"],
            "pedido_id": d["pedido_id"],
            "codigo_rastreio": d["codigo_rastreio"],
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(**d),
            "criada_em": evento.ocorrido_em,
        })
    _processados.add(evento.id)
    return {"processado": True}


@app.get("/notificacoes")
def listar(email: str):
    return [n for n in _notificacoes if n["email"] == email]


@app.post("/admin/reset", status_code=204)
def reset():
    _notificacoes.clear()
    _processados.clear()
    return Response(status_code=204)

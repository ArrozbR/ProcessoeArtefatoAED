# arquivo: servicos/notificacoes/main.py
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."

_estado = {"contador": 0, "notificacoes": [], "processados": set()}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem="Dados inválidos."):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _registrar(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em):
    numero = _estado["contador"] + 1
    _estado["contador"] = numero
    _estado["notificacoes"].append({
        "id": f"NOT-{numero:06d}",
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo_rastreio,
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["notificacoes"].clear()
    _estado["processados"].clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await _ler_json(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _invalido(f"Campo '{campo}' ausente.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' inválido.")

    evento_id = corpo["id"]
    try:
        hash(evento_id)
    except TypeError:
        raise _invalido("Campo 'id' inválido.")
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]

    if evento_id in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    email = dados.get("destinatario_email")

    if tipo == "envio.criado":
        mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=codigo)
        _registrar(email, pedido_id, codigo, "CRIADO", mensagem, ocorrido_em)
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS_STATUS:
            mensagem = MENSAGENS_STATUS[status].format(pedido_id=pedido_id)
            _registrar(email, pedido_id, codigo, status, mensagem, ocorrido_em)
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
async def listar_notificacoes(email: str = Query(...)):
    lista = [n for n in _estado["notificacoes"] if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)

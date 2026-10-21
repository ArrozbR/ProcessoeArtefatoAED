# arquivo: servicos/notificacoes/main.py
import copy

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_estado = {"notificacoes": [], "contador": 0, "processados": set()}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


_INVALIDO = object()


async def ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def chave_evento(valor):
    if isinstance(valor, str):
        return valor
    return repr(valor)


def registrar(tipo, dados, ocorrido_em):
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    email = dados["destinatario_email"]
    mensagem = MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo)
    _estado["contador"] += 1
    _estado["notificacoes"].append(
        {
            "id": f"NOT-{_estado['contador']:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": ocorrido_em,
        }
    )


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    _estado["processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos(f"Envelope sem o campo '{campo}'.")

    chave = chave_evento(corpo["id"])
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if tipo not in ("envio.criado", "envio.status_atualizado"):
        return JSONResponse(status_code=200, content={"processado": False})
    if not isinstance(dados, dict):
        return dados_invalidos("Campo 'dados' inválido.")

    if tipo == "envio.criado":
        tipo_notificacao = "CRIADO"
    else:
        tipo_notificacao = dados.get("status")

    if tipo_notificacao in MENSAGENS:
        for campo in ("pedido_id", "codigo_rastreio", "destinatario_email"):
            if campo not in dados:
                return dados_invalidos(f"Campo 'dados.{campo}' ausente.")
        registrar(tipo_notificacao, dados, corpo["ocorrido_em"])

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return dados_invalidos("Parâmetro 'email' é obrigatório.")
    itens = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=itens)

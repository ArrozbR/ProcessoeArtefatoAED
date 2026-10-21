# arquivo: servicos/notificacoes/main.py
import json

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

_estado = {"notificacoes": [], "processados": set(), "contador": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        corpo = await request.body()
        dados = json.loads(corpo)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def processado(valor: bool) -> JSONResponse:
    return JSONResponse(status_code=200, content={"processado": valor})


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["processados"] = set()
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    ev = await ler_json(request)
    if ev is None:
        return invalido("Corpo da requisição inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in ev:
            return invalido(f"Campo '{campo}' ausente no envelope.")
    eid = ev["id"]
    tipo = ev["tipo"]
    dados = ev["dados"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        return invalido("Envelope inválido.")

    if eid in _estado["processados"]:
        return processado(False)

    if tipo == "envio.criado":
        status = "CRIADO"
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
    else:
        return processado(False)

    if status in MENSAGENS:
        try:
            pedido_id = dados["pedido_id"]
            codigo = dados["codigo_rastreio"]
            email = dados["destinatario_email"]
        except KeyError:
            return invalido("Dados do evento inválidos.")
        _estado["contador"] += 1
        notificacao = {
            "id": f"NOT-{_estado['contador']:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": status,
            "mensagem": MENSAGENS[status].format(pedido_id=pedido_id, codigo_rastreio=codigo),
            "criada_em": ev["ocorrido_em"],
        }
        _estado["notificacoes"].append(notificacao)

    _estado["processados"].add(eid)
    return processado(True)


@app.get("/notificacoes")
async def listar(request: Request):
    if "email" not in request.query_params:
        return invalido("Parâmetro 'email' é obrigatório.")
    email = request.query_params["email"]
    lista = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)

# arquivo: servicos/notificacoes/main.py
import copy
from typing import Any, Dict, Optional

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

_estado: Dict[str, Any] = {"processados": set(), "contador": 0, "notificacoes": []}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.erro, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."},
    )


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": erro, "mensagem": str(exc.detail)})


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _exigir(dados: Dict[str, Any], campos) -> None:
    for c in campos:
        if c not in dados:
            raise _invalido(f"Campo 'dados.{c}' ausente.")


def _registrar(tipo: str, dados: Dict[str, Any], ocorrido_em: Any) -> None:
    numero = _estado["contador"] + 1
    _estado["contador"] = numero
    _estado["notificacoes"].append(
        {
            "id": f"NOT-{numero:06d}",
            "email": dados["destinatario_email"],
            "pedido_id": dados["pedido_id"],
            "codigo_rastreio": dados["codigo_rastreio"],
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(
                pedido_id=dados["pedido_id"], codigo_rastreio=dados["codigo_rastreio"]
            ),
            "criada_em": ocorrido_em,
        }
    )


@app.post("/eventos")
async def consumir_evento(request: Request):
    corpo = await _ler_corpo(request)
    if not isinstance(corpo, dict):
        raise _invalido("O envelope deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    chave = str(corpo["id"])
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]

    if chave in _estado["processados"]:
        return {"processado": False}

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("destinatario_email", "pedido_id", "codigo_rastreio"))
        _registrar("CRIADO", dados, ocorrido_em)
        _estado["processados"].add(chave)
        return {"processado": True}

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("status",))
        status = dados["status"]
        if status in MENSAGENS:
            _exigir(dados, ("destinatario_email", "pedido_id", "codigo_rastreio"))
            _registrar(status, dados, ocorrido_em)
        _estado["processados"].add(chave)
        return {"processado": True}

    return {"processado": False}


@app.get("/notificacoes")
async def listar_notificacoes(email: Optional[str] = None):
    if email is None:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return copy.deepcopy(itens)


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["contador"] = 0
    _estado["notificacoes"] = []
    return Response(status_code=204)

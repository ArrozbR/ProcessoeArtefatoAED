# arquivo: servicos/transportadoras/main.py
import re
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras: dict = {}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _int_campo(corpo: dict, campo: str, minimo: int, maximo=None) -> int:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if not _eh_int(valor):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if valor < minimo or (maximo is not None and valor > maximo):
        raise _invalido(f"Campo '{campo}' fora das restrições.")
    return valor


def _parse_bool_query(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


def _copia(t: dict) -> dict:
    return {
        "id": t["id"],
        "nome": t["nome"],
        "ufs_atendidas": list(t["ufs_atendidas"]),
        "tarifa_base_centavos": t["tarifa_base_centavos"],
        "tarifa_kg_centavos": t["tarifa_kg_centavos"],
        "adicional_interestadual_pct": t["adicional_interestadual_pct"],
        "prazo_dias_uteis": t["prazo_dias_uteis"],
        "dias_adicionais_interestadual": t["dias_adicionais_interestadual"],
        "peso_maximo_g": t["peso_maximo_g"],
        "ativa": t["ativa"],
    }


@app.post("/transportadoras")
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)

    if "id" not in corpo:
        raise _invalido("Campo 'id' ausente.")
    tid = corpo["id"]
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise _invalido("Campo 'id' inválido.")

    if "nome" not in corpo:
        raise _invalido("Campo 'nome' ausente.")
    nome = corpo["nome"]
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")

    if "ufs_atendidas" not in corpo:
        raise _invalido("Campo 'ufs_atendidas' ausente.")
    ufs = corpo["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' deve ser uma lista não vazia.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")

    tarifa_base = _int_campo(corpo, "tarifa_base_centavos", 0)
    tarifa_kg = _int_campo(corpo, "tarifa_kg_centavos", 0)
    adicional = _int_campo(corpo, "adicional_interestadual_pct", 0, 100)
    prazo = _int_campo(corpo, "prazo_dias_uteis", 1)
    dias_adic = _int_campo(corpo, "dias_adicionais_interestadual", 0)
    peso_max = _int_campo(corpo, "peso_maximo_g", 1)

    with _lock:
        if tid in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", f"Já existe transportadora com id '{tid}'.")
        t = {
            "id": tid,
            "nome": nome,
            "ufs_atendidas": list(ufs),
            "tarifa_base_centavos": tarifa_base,
            "tarifa_kg_centavos": tarifa_kg,
            "adicional_interestadual_pct": adicional,
            "prazo_dias_uteis": prazo,
            "dias_adicionais_interestadual": dias_adic,
            "peso_maximo_g": peso_max,
            "ativa": True,
        }
        _transportadoras[tid] = t
        resposta = _copia(t)
    return JSONResponse(status_code=201, content=resposta)


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    params = request.query_params
    somente_ativas = False
    if "somente_ativas" in params:
        somente_ativas = _parse_bool_query(params["somente_ativas"])
    with _lock:
        itens = [
            _copia(t)
            for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])
            if (t["ativa"] or not somente_ativas)
        ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora '{tid}' não encontrada.")
        resposta = _copia(t)
    return JSONResponse(status_code=200, content=resposta)


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo:
        raise _invalido("Campo 'ativa' ausente.")
    ativa = corpo["ativa"]
    if not isinstance(ativa, bool):
        raise _invalido("Campo 'ativa' deve ser booleano.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", f"Transportadora '{tid}' não encontrada.")
        t["ativa"] = ativa
        resposta = _copia(t)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)

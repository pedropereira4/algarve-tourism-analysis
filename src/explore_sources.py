"""Exploração das fontes de dados: INE (API JSON) e RNAL (webservice do Turismo de Portugal).

Este script só lê e imprime o que encontra. Não grava ficheiros nem faz ingestão.

Uso:
    uv run python src/explore_sources.py                  # INE e RNAL
    uv run python src/explore_sources.py --fonte ine
    uv run python src/explore_sources.py --fonte rnal --todos-concelhos

Notas:
- A API JSON do INE não tem pesquisa: os códigos são procurados na pesquisa da base de dados do
  portal do INE (HTML) e depois confirmados no endpoint de metadados da API JSON.
- Os certificados são verificados com a loja de certificados do sistema (truststore), porque
  antivírus que inspecionam HTTPS (p. ex. Avast) fazem falhar o bundle do certifi.
- O RNAL inclui dados pessoais dos titulares (nome, NIF, email). Este script nunca os imprime.
"""

import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import date
from typing import Any

import requests

INE_META_URL = "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp"
INE_DADOS_URL = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
INE_PESQUISA_URL = "https://www.ine.pt/xportal/xmain"
RNAL_URL = "https://webservices.turismodeportugal.pt/RNT_External/rest/RNT/list_RNAL"

# O portal do INE redireciona pedidos com o User-Agent por omissão do requests.
USER_AGENT = "Mozilla/5.0 (compatible; data-exploration-script)"
PAUSA_S = 0.5  # pausa entre pedidos: o INE responde 503 quando recebe muitos seguidos

# Conceito -> (termo de pesquisa no portal do INE, regex que o título do indicador deve cumprir)
CONCEITOS_INE: dict[str, tuple[str, str]] = {
    "Dormidas": ("dormidas", r"^Dormidas \(N\.º\) nos estabelecimentos de alojamento turístico"),
    "Hóspedes": ("hóspedes", r"^Hóspedes \(N\.º\) nos estabelecimentos de alojamento turístico"),
    "Proveitos totais": ("proveitos totais", r"^Proveitos totais \(€\) nos estabelecimentos"),
    "Proveitos de aposento": ("proveitos de aposento", r"^Proveitos de aposento \(€\) nos"),
    "Taxa de ocupação-quarto": ("ocupação-quarto", r"^Taxa de ocupação-quarto"),
    "RevPAR": ("Rev Par", r"Rev ?Par"),
    "ADR": ("quarto ocupado", r"\bADR\b|quarto ocupado"),
}
# Só interessam séries por região NUTS com o desdobramento por tipo/segmento de alojamento
FILTRO_REGIONAL = re.compile(
    r"Localização geográfica \(NUTS - (2013|2024)\) e (Tipo|Segmento) \(alojamento turístico\)"
)

CONCELHOS_ALGARVE = [
    "Albufeira",
    "Alcoutim",
    "Aljezur",
    "Castro Marim",
    "Faro",
    "Lagoa (Algarve)",
    "Lagos",
    "Loulé",
    "Monchique",
    "Olhão",
    "Portimão",
    "São Brás de Alportel",
    "Silves",
    "Tavira",
    "Vila do Bispo",
    "Vila Real de Santo António",
]
CAMPOS_REDIGIDOS = {"Endereco"}
SUBCAMPOS_TITULAR_VISIVEIS = {"Tipo"}  # Nome, Contribuinte e Email nunca são impressos
REGEX_CAMPO_ESTADO = re.compile(r"estado|situa|cancel|cessa|ativo|activ|baixa", re.I)


# --------------------------------------------------------------------------------------------
# Utilitários


def titulo(texto: str) -> None:
    print(f"\n{'=' * 100}\n{texto}\n{'=' * 100}")


def subtitulo(texto: str) -> None:
    print(f"\n--- {texto} ---")


def nova_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers["User-Agent"] = USER_AGENT
    return sessao


def obter(sessao: requests.Session, url: str, params: dict[str, Any]) -> requests.Response:
    """GET com novas tentativas para os 503 / respostas não-JSON intermitentes do INE."""
    ultimo_erro = ""
    for tentativa in range(4):
        try:
            resposta = sessao.get(url, params=params, timeout=120)
            if resposta.status_code == 200:
                return resposta
            ultimo_erro = f"HTTP {resposta.status_code}"
        except requests.exceptions.SSLError:
            raise
        except requests.RequestException as erro:
            ultimo_erro = repr(erro)
        time.sleep(5 * (tentativa + 1))
    raise RuntimeError(f"Falhou {url} {params}: {ultimo_erro}")


def obter_json(sessao: requests.Session, url: str, params: dict[str, Any]) -> Any:
    for tentativa in range(3):
        resposta = obter(sessao, url, params)
        try:
            return resposta.json()
        except ValueError:
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError(f"Resposta não-JSON de {url} {params}")


def limpar_html(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", texto))).strip()


# --------------------------------------------------------------------------------------------
# INE


_LINK_INDICADOR = re.compile(r"<a[^>]+indOcorrCod=(\d{7})[^>]*>(.*?)</a>", re.S)


def pesquisar_ine(sessao: requests.Session, termo: str, max_paginas: int = 10) -> dict[str, str]:
    """Pesquisa na base de dados do portal do INE. Devolve {código: título}."""
    encontrados: dict[str, str] = {}
    for pagina in range(1, max_paginas + 1):
        params = {
            "xpid": "INE",
            "xlang": "pt",
            "xpgid": "ine_pesquisa",
            "frm_accao": "PESQUISAR",
            "frm_show_page_num": pagina,
            "frm_modo_pesquisa": "PESQUISA_SIMPLES",
            "frm_texto": termo,
            "frm_modo_texto": "MODO_TEXTO_ALL",
            "frm_tema": "QUALQUER_TEMA",
            "frm_area": "o_ine_area_BaseDados",
        }
        texto = obter(sessao, INE_PESQUISA_URL, params).text
        novos = 0
        for codigo, titulo_html in _LINK_INDICADOR.findall(texto):
            nome = limpar_html(titulo_html)
            if nome and codigo not in encontrados:
                encontrados[codigo] = nome
                novos += 1
        if not novos:
            break
        time.sleep(PAUSA_S)
    return encontrados


def periodicidade(titulo_indicador: str) -> str:
    """'...; Mensal' -> 'Mensal'."""
    return titulo_indicador.rsplit(";", 1)[-1].strip() if ";" in titulo_indicador else "?"


def classificar_indicadores(
    resultados: dict[str, str], regex_conceito: str
) -> dict[str, list[tuple[str, str]]]:
    """Filtra os resultados a séries regionais do conceito e agrupa-os por periodicidade."""
    grupos: dict[str, list[tuple[str, str]]] = {}
    for codigo, nome in sorted(resultados.items()):
        if re.search(regex_conceito, nome) and FILTRO_REGIONAL.search(nome):
            grupos.setdefault(periodicidade(nome), []).append((codigo, nome))
    return grupos


def categorias(meta: dict[str, Any]) -> list[dict[str, str]]:
    return [c for lista in meta["Dimensoes"]["Categoria_Dim"][0].values() for c in lista]


def dimensao_geografica(meta: dict[str, Any]) -> str | None:
    for dim in meta["Dimensoes"]["Descricao_Dim"]:
        if dim["abrv"].startswith("Localização geográfica"):
            return dim["dim_num"]
    return None


def regioes_nuts2(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """Regiões NUTS II (nível 3 na hierarquia do INE: Portugal > Continente/RA > NUTS II)."""
    dim_geo = dimensao_geografica(meta)
    return [
        (c["categ_cod"], c["categ_dsg"])
        for c in categorias(meta)
        if c["dim_num"] == dim_geo and c["categ_nivel"] == "3"
    ]


def mostrar_metadados(meta: dict[str, Any]) -> None:
    print(f"\n{meta['IndicadorCod']}  {meta['IndicadorNome']}")
    n_periodos = sum(1 for c in categorias(meta) if c["dim_num"] == "1")
    print(
        f"  período: {meta['PrimeiroPeriodo']} -> {meta['UltimoPeriodo']} ({n_periodos} períodos)"
        f" | unidade: {meta['UnidadeMedida']} | última atualização: {meta['DataUltimaAtualizacao']}"
    )
    dim_geo = dimensao_geografica(meta)
    for dim in meta["Dimensoes"]["Descricao_Dim"]:
        linha = f"  dim {dim['dim_num']}: {dim['abrv']}"
        cats = [c for c in categorias(meta) if c["dim_num"] == dim["dim_num"]]
        if dim["dim_num"] == dim_geo:
            nuts2 = regioes_nuts2(meta)
            linha += f" — {len(nuts2)} NUTS II: " + ", ".join(f"{n} [{c}]" for c, n in nuts2)
        elif dim["dim_num"] != "1":
            linha += " — " + ", ".join(f"{c['categ_dsg']} [{c['categ_cod']}]" for c in cats)
        print(linha)
        if dim.get("nota_dsg"):
            print(f"         nota: {dim['nota_dsg']}")


def explorar_ine(sessao: requests.Session) -> None:
    titulo("INE — indicadores de alojamento turístico por região NUTS II")
    print("Pesquisa na base de dados do portal do INE; confirmação no endpoint de metadados:")
    print(f"  {INE_META_URL}?varcd=<código>&lang=PT")

    detalhar: list[str] = []
    for conceito, (termo, regex) in CONCEITOS_INE.items():
        subtitulo(f"{conceito} (pesquisa: {termo!r})")
        grupos = classificar_indicadores(pesquisar_ine(sessao, termo), regex)
        if not grupos:
            print("  nenhum indicador regional encontrado")
            continue
        for per, lista in sorted(grupos.items()):
            for codigo, nome in lista:
                print(f"  {codigo} [{per}] {nome}")
        escolhidos = grupos.get("Mensal") or next(iter(grupos.values()))
        if "Mensal" not in grupos:
            print("  -> não há série mensal por NUTS; detalha-se a(s) de outra periodicidade")
        detalhar += [codigo for codigo, _ in escolhidos]

    subtitulo("Metadados (período disponível, dimensões e regiões NUTS II)")
    metadados: dict[str, dict[str, Any]] = {}
    for codigo in detalhar:
        meta = obter_json(sessao, INE_META_URL, {"varcd": codigo, "lang": "PT"})[0]
        metadados[codigo] = meta
        mostrar_metadados(meta)
        time.sleep(PAUSA_S)

    # Exemplo de resposta: a primeira série mensal de dormidas em NUTS 2013 (é a que cobre 2019)
    exemplo = next(
        (
            c
            for c, m in metadados.items()
            if m["IndicadorNome"].startswith("Dormidas")
            and "NUTS - 2013" in m["IndicadorNome"]
            and m["Periodic"] == "Mensal"
        ),
        None,
    )
    if exemplo:
        algarve = next(c for c, n in regioes_nuts2(metadados[exemplo]) if n == "Algarve")
        params = {"op": "2", "varcd": exemplo, "Dim1": "S3A201908", "Dim2": algarve, "lang": "PT"}
        subtitulo("Exemplo de resposta (dados)")
        print(f"GET {INE_DADOS_URL}?" + "&".join(f"{k}={v}" for k, v in params.items()))
        print(json.dumps(obter_json(sessao, INE_DADOS_URL, params), ensure_ascii=False, indent=2))
        print(
            "Notas: sem Dim1 a API devolve só o último período; Dim1=T devolve todos. "
            "Os meses têm o formato S3A<AAAAMM>."
        )


# --------------------------------------------------------------------------------------------
# RNAL


def redigir_registo(registo: dict[str, Any]) -> dict[str, Any]:
    """Cópia do registo sem dados pessoais do titular nem morada."""
    limpo: dict[str, Any] = {}
    for campo, valor in registo.items():
        if campo == "TitulardaExploracao" and isinstance(valor, dict):
            limpo[campo] = {
                k: (v if k in SUBCAMPOS_TITULAR_VISIVEIS else "<redigido>")
                for k, v in valor.items()
            }
        elif campo in CAMPOS_REDIGIDOS:
            limpo[campo] = "<redigido>"
        else:
            limpo[campo] = valor
    return limpo


def resumir_datas(datas: list[str | None], corte: date = date(2023, 1, 1)) -> dict[str, Any]:
    """Resumo das datas de registo (formato AAAA-MM-DD)."""
    validas: list[date] = []
    invalidas = 0
    for texto in datas:
        try:
            validas.append(date.fromisoformat(texto or ""))
        except ValueError:
            invalidas += 1
    return {
        "n": len(datas),
        "vazias_ou_invalidas": invalidas,
        "min": min(validas, default=None),
        "max": max(validas, default=None),
        "antes_do_corte": sum(d < corte for d in validas),
        "desde_o_corte": sum(d >= corte for d in validas),
        "antes_de_2008": sum(d.year < 2008 for d in validas),
        "por_ano": dict(sorted(Counter(d.year for d in validas).items())),
    }


def tipos_dos_campos(registos: list[dict[str, Any]]) -> dict[str, str]:
    tipos: dict[str, set[str]] = {}
    preenchidos: Counter[str] = Counter()
    for reg in registos:
        for campo, valor in reg.items():
            tipos.setdefault(campo, set()).add(type(valor).__name__)
            if valor not in (None, ""):
                preenchidos[campo] += 1
    return {
        campo: f"{'/'.join(sorted(t))}, preenchido em {preenchidos[campo]}/{len(registos)}"
        for campo, t in tipos.items()
    }


def listar_rnal(sessao: requests.Session, concelho: str) -> list[dict[str, Any]]:
    dados = obter_json(sessao, RNAL_URL, {"Concelho": concelho})
    return [item.get("RNAL_Registo", item) for item in dados]


def explorar_rnal(sessao: requests.Session, todos_concelhos: bool) -> None:
    titulo("RNAL — webservice do Turismo de Portugal")
    print(f"GET {RNAL_URL}?Concelho=Lagoa")
    print(f"  -> {len(listar_rnal(sessao, 'Lagoa'))} registos")
    print(
        "  O filtro exige o nome exato do concelho. 'Lagoa' existe no Algarve e nos Açores; "
        "no RNAL o do Algarve chama-se 'Lagoa (Algarve)'."
    )

    concelho = "Lagoa (Algarve)"
    registos = listar_rnal(sessao, concelho)
    print(f"\nGET {RNAL_URL}?Concelho={concelho}")
    print(f"  -> {len(registos)} registos")
    if not registos:
        return

    subtitulo("Campos disponíveis")
    for campo, descricao in tipos_dos_campos(registos).items():
        print(f"  {campo}: {descricao}")
    subcampos = Counter(
        k
        for r in registos
        if isinstance(r.get("TitulardaExploracao"), dict)
        for k in r["TitulardaExploracao"]
    )
    print(f"  TitulardaExploracao.*: {', '.join(subcampos)}  (dados pessoais: não impressos)")
    campos_estado = [c for c in registos[0] if REGEX_CAMPO_ESTADO.search(c)]
    print(f"  Campo de estado / data de cancelamento: {campos_estado or 'nenhum'}")
    n_unicos = len({r.get("NrRegisto") for r in registos})
    print(f"  NrRegisto únicos: {n_unicos} de {len(registos)}")

    subtitulo("Exemplo de registo (redigido)")
    print(json.dumps(redigir_registo(registos[0]), ensure_ascii=False, indent=2))

    subtitulo("Distribuições")
    for campo in ("Modalidade", "Freguesia", "Distrito"):
        print(f"  {campo}: {dict(Counter(r.get(campo) for r in registos).most_common())}")
    tipos_titular = Counter(
        (r.get("TitulardaExploracao") or {}).get("Tipo") for r in registos
    ).most_common()
    print(f"  Tipo de titular: {dict(tipos_titular)}")

    subtitulo("DataRegisto")
    resumo = resumir_datas([r.get("DataRegisto") for r in registos])
    print(f"  presente e válida em {resumo['n'] - resumo['vazias_ou_invalidas']}/{resumo['n']}")
    print(f"  intervalo: {resumo['min']} -> {resumo['max']}")
    print(f"  antes de 2023: {resumo['antes_do_corte']} | desde 2023: {resumo['desde_o_corte']}")
    print(f"  anteriores a 2008 (suspeitas): {resumo['antes_de_2008']}")
    print(f"  por ano: {resumo['por_ano']}")

    if todos_concelhos:
        subtitulo("Os 16 concelhos do Algarve")
        print(f"  {'concelho':<28}{'registos':>9}{'antes 2023':>12}{'desde 2023':>12}  intervalo")
        for nome in CONCELHOS_ALGARVE:
            regs = registos if nome == concelho else listar_rnal(sessao, nome)
            r = resumir_datas([x.get("DataRegisto") for x in regs])
            print(
                f"  {nome:<28}{r['n']:>9}{r['antes_do_corte']:>12}{r['desde_o_corte']:>12}"
                f"  {r['min']} -> {r['max']}"
            )
            time.sleep(PAUSA_S)


# --------------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fonte", choices=["ine", "rnal", "todas"], default="todas")
    parser.add_argument(
        "--todos-concelhos",
        action="store_true",
        help="no RNAL, conta também os registos dos 16 concelhos do Algarve (~30 MB)",
    )
    args = parser.parse_args()

    import truststore

    truststore.inject_into_ssl()
    sys.stdout.reconfigure(encoding="utf-8")

    sessao = nova_sessao()
    if args.fonte in ("ine", "todas"):
        explorar_ine(sessao)
    if args.fonte in ("rnal", "todas"):
        explorar_rnal(sessao, args.todos_concelhos)


if __name__ == "__main__":
    main()

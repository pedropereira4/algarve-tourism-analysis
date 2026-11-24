"""Testes das funções puras de src/explore_sources.py (sem acesso à rede)."""

from datetime import date

from explore_sources import CONCEITOS_INE, classificar_indicadores, redigir_registo, resumir_datas


def test_redigir_registo_nao_expoe_dados_pessoais():
    registo = {
        "NrRegisto": "1/AL",
        "Endereco": "Rua X, 1",
        "TitulardaExploracao": {
            "Tipo": "Pessoa coletiva",
            "Nome": "Nome",
            "Contribuinte": "123456789",
            "Email": "a@exemplo.pt",
            "Telemovel": "900000000",
        },
    }
    limpo = redigir_registo(registo)
    assert limpo["NrRegisto"] == "1/AL"
    assert limpo["Endereco"] == "<redigido>"
    titular = limpo["TitulardaExploracao"]
    assert titular["Tipo"] == "Pessoa coletiva"
    assert all(v == "<redigido>" for k, v in titular.items() if k != "Tipo")
    assert registo["Endereco"] == "Rua X, 1"  # o original não é alterado


def test_resumir_datas():
    resumo = resumir_datas(["2022-12-31", "2023-01-01", "2024-05-02", "1968-10-15", "", None, "x"])
    assert resumo["vazias_ou_invalidas"] == 3
    assert resumo["antes_do_corte"] == 2
    assert resumo["desde_o_corte"] == 2
    assert resumo["antes_de_2008"] == 1
    assert resumo["min"] == date(1968, 10, 15)
    assert resumo["max"] == date(2024, 5, 2)
    assert resumo["por_ano"] == {1968: 1, 2022: 1, 2023: 1, 2024: 1}


def test_classificar_indicadores_filtra_series_regionais_e_agrupa_por_periodicidade():
    base = "Dormidas (N.º) nos estabelecimentos de alojamento turístico por Localização geográfica"
    resultados = {
        "0009808": f"{base} (NUTS - 2013) e Tipo (alojamento turístico); Mensal",
        "0013214": f"{base} (NUTS - 2024) e Tipo (alojamento turístico); Anual",
        "0009182": f"{base} (NUTS - 2013) e Local de residência (País); Anual",
        "0001542": "Dormidas (N.º) nos estabelecimentos hoteleiros por Localização geográfica "
        "(NUTS - 2013) e Tipo (estabelecimento hoteleiro); Mensal",
    }
    grupos = classificar_indicadores(resultados, CONCEITOS_INE["Dormidas"][1])
    assert {per: [c for c, _ in lista] for per, lista in grupos.items()} == {
        "Mensal": ["0009808"],
        "Anual": ["0013214"],
    }

"""Controle simples de atendimentos e estoque para podologia.

Dependência da interface: pip install customtkinter
O SQLite já vem com o Python. Os dados ficam em clinica.db.
Que se encontra localizado na pasta local do PC com o nome ClinicaPodologia
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

try:
    import customtkinter as ctk
except ImportError as exc:
    raise SystemExit(
        "Falta instalar CustomTkinter. Abra o terminal e execute:\n\n"
        "python -m pip install customtkinter"
    ) from exc


APP_DATA_DIR = Path.home() / "AppData" / "Local" / "ClinicaPodologia"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DATA_DIR / "clinica.db"


class Banco:
    def __init__(self, caminho: Path = DB_PATH):
        self.con = sqlite3.connect(caminho)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.criar_tabelas()

    def criar_tabelas(self) -> None:
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL COLLATE NOCASE UNIQUE,
                estoque REAL NOT NULL DEFAULT 0 CHECK (estoque >= 0),
                unidade TEXT NOT NULL DEFAULT 'un'
            );
            CREATE TABLE IF NOT EXISTS servicos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL COLLATE NOCASE UNIQUE,
                preco REAL NOT NULL DEFAULT 0 CHECK (preco >= 0)
            );
            CREATE TABLE IF NOT EXISTS servico_produtos (
                servico_id INTEGER NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
                produto_id INTEGER NOT NULL REFERENCES produtos(id),
                quantidade REAL NOT NULL CHECK (quantidade > 0),
                PRIMARY KEY (servico_id, produto_id)
            );
            CREATE TABLE IF NOT EXISTS atendimentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                cliente TEXT NOT NULL,
                servico_id INTEGER NOT NULL REFERENCES servicos(id),
                preco REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atendimento_produtos (
                atendimento_id INTEGER NOT NULL REFERENCES atendimentos(id) ON DELETE CASCADE,
                produto_id INTEGER NOT NULL REFERENCES produtos(id),
                nome_produto TEXT NOT NULL,
                unidade TEXT NOT NULL,
                quantidade REAL NOT NULL,
                PRIMARY KEY (atendimento_id, produto_id)
            );
        """)
        self.con.commit()

    def fechar(self) -> None:
        self.con.close()

    def produtos(self):
        return self.con.execute("SELECT * FROM produtos ORDER BY nome").fetchall()

    def servicos(self):
        return self.con.execute("SELECT * FROM servicos ORDER BY nome").fetchall()

    def salvar_produto(self, nome: str, estoque: float, unidade: str) -> None:
        self.con.execute("INSERT INTO produtos(nome, estoque, unidade) VALUES (?, ?, ?)",
                         (nome, estoque, unidade))
        self.con.commit()

    def atualizar_estoque(self, produto_id: int, quantidade: float) -> None:
        cur = self.con.execute("UPDATE produtos SET estoque = estoque + ? WHERE id = ? AND estoque + ? >= 0",
                               (quantidade, produto_id, quantidade))
        if cur.rowcount == 0:
            raise ValueError("A baixa deixaria o estoque negativo.")
        self.con.commit()

    def salvar_servico(self, nome: str, preco: float) -> None:
        self.con.execute("INSERT INTO servicos(nome, preco) VALUES (?, ?)", (nome, preco))
        self.con.commit()

    def salvar_composicao(self, servico_id: int, produto_id: int, quantidade: float) -> None:
        self.con.execute("""INSERT INTO servico_produtos(servico_id, produto_id, quantidade)
            VALUES (?, ?, ?) ON CONFLICT(servico_id, produto_id)
            DO UPDATE SET quantidade = excluded.quantidade""",
                         (servico_id, produto_id, quantidade))
        self.con.commit()

    def composicao(self, servico_id: int):
        return self.con.execute("""SELECT p.nome, p.unidade, sp.quantidade
            FROM servico_produtos sp JOIN produtos p ON p.id = sp.produto_id
            WHERE sp.servico_id = ? ORDER BY p.nome""", (servico_id,)).fetchall()

    def registrar_atendimento(self, cliente: str, servico_id: int, consumos: dict[int, float], preco_final: float) -> None:
        """Salva atendimento e baixa todos os materiais numa única transação."""
        cur = self.con.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            servico = cur.execute("SELECT preco FROM servicos WHERE id = ?", (servico_id,)).fetchone()
            if servico is None:
                raise ValueError("Selecione um serviço válido.")
            if preco_final < 0:
                raise ValueError("O valor do atendimento não pode ser negativo.")
            materiais = cur.execute("""SELECT p.id, p.nome, p.estoque, p.unidade
                FROM servico_produtos sp JOIN produtos p ON p.id = sp.produto_id
                WHERE sp.servico_id = ?""", (servico_id,)).fetchall()
            por_id = {m['id']: m for m in materiais}
            if any(pid not in por_id or qtd < 0 for pid, qtd in consumos.items()):
                raise ValueError("A lista de materiais foi alterada. Selecione o serviço novamente.")
            faltas = [f"{por_id[pid]['nome']} (precisa {qtd:g} {por_id[pid]['unidade']}, tem {por_id[pid]['estoque']:g})"
                      for pid, qtd in consumos.items() if por_id[pid]['estoque'] < qtd]
            if faltas:
                raise ValueError("Estoque insuficiente:\n" + "\n".join(faltas))
            for pid, qtd in consumos.items():
                cur.execute("UPDATE produtos SET estoque = estoque - ? WHERE id = ?", (qtd, pid))
            cur.execute("INSERT INTO atendimentos(data, cliente, servico_id, preco) VALUES (?, ?, ?, ?)",
                        (datetime.now().isoformat(timespec="minutes"), cliente, servico_id, preco_final))
            atendimento_id = cur.lastrowid
            for pid, qtd in consumos.items():
                m = por_id[pid]
                cur.execute("INSERT INTO atendimento_produtos VALUES (?, ?, ?, ?, ?)",
                            (atendimento_id, pid, m['nome'], m['unidade'], qtd))
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    def relatorio_historico(self, inicio: str, fim: str):
        return self.con.execute("""SELECT a.id, a.data, a.cliente, s.nome AS servico, a.preco,
                ap.nome_produto, ap.unidade, ap.quantidade
            FROM atendimentos a
            JOIN servicos s ON s.id = a.servico_id
            LEFT JOIN atendimento_produtos ap ON ap.atendimento_id = a.id
            WHERE date(a.data) BETWEEN date(?) AND date(?)
            ORDER BY a.id DESC, ap.nome_produto""", (inicio, fim)).fetchall()

    def balanco(self, inicio: str, fim: str):
        return self.con.execute("""SELECT s.nome AS servico, COUNT(*) AS quantidade,
                SUM(a.preco) AS total
            FROM atendimentos a JOIN servicos s ON s.id = a.servico_id
            WHERE date(a.data) BETWEEN date(?) AND date(?)
            GROUP BY s.id, s.nome ORDER BY s.nome""", (inicio, fim)).fetchall()


class Aplicativo(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Clínica — atendimentos e estoque")
        self.geometry("900x650")
        self.minsize(760, 560)
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.banco = Banco()
        self.protocol("WM_DELETE_WINDOW", self.fechar)

        ctk.CTkLabel(self, text="Controle de serviços", font=ctk.CTkFont(size=26, weight="bold"))\
            .pack(anchor="w", padx=24, pady=(20, 4))
        ctk.CTkLabel(self, text="controle basico de podologia").pack(anchor="w", padx=24, pady=(0, 14))
        self.abas = ctk.CTkTabview(self)
        self.abas.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.aba_atendimento = self.abas.add("Novo atendimento")
        self.aba_produtos = self.abas.add("Produtos / estoque")
        self.aba_servicos = self.abas.add("Serviços")
        self.aba_composicao = self.abas.add("Materiais por serviço")
        self.aba_historico = self.abas.add("Histórico")
        self.aba_balanco = self.abas.add("Balanço")
        self.montar_atendimento()
        self.montar_produtos()
        self.montar_servicos()
        self.montar_composicao()
        self.montar_historico()
        self.montar_balanco()
        self.atualizar_telas()

    def campo(self, pai, rotulo: str, placeholder: str = ""):
        ctk.CTkLabel(pai, text=rotulo).pack(anchor="w", padx=22, pady=(14, 4))
        entrada = ctk.CTkEntry(pai, placeholder_text=placeholder, width=360)
        entrada.pack(anchor="w", padx=22)
        return entrada

    def montar_atendimento(self):
        ctk.CTkLabel(self.aba_atendimento, text="Registrar atendimento", font=ctk.CTkFont(size=21, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(20, 2))
        self.cliente = self.campo(self.aba_atendimento, "Nome da cliente", "Ex.: Maria")
        ctk.CTkLabel(self.aba_atendimento, text="Serviço").pack(anchor="w", padx=22, pady=(14, 4))
        self.escolha_servico = ctk.CTkComboBox(self.aba_atendimento, values=["Cadastre um serviço primeiro"], width=360)
        self.escolha_servico.pack(anchor="w", padx=22)
        ctk.CTkLabel(self.aba_atendimento, text="Valor e desconto deste atendimento").pack(anchor="w", padx=22, pady=(12, 4))
        linha_valores = ctk.CTkFrame(self.aba_atendimento, fg_color="transparent")
        linha_valores.pack(anchor="w", padx=16)
        ctk.CTkLabel(linha_valores, text="Valor (R$)").pack(side="left", padx=6)
        self.valor_atendimento = ctk.CTkEntry(linha_valores, width=120)
        self.valor_atendimento.pack(side="left", padx=6)
        ctk.CTkLabel(linha_valores, text="Desconto (%)").pack(side="left", padx=(18, 6))
        self.desconto_atendimento = ctk.CTkEntry(linha_valores, width=100)
        self.desconto_atendimento.pack(side="left", padx=6)
        self.desconto_atendimento.insert(0, "0")
        self.valor_final_label = ctk.CTkLabel(self.aba_atendimento, text="Valor final: R$ 0,00")
        self.valor_final_label.pack(anchor="w", padx=22, pady=(2, 4))
        self.valor_atendimento.bind("<KeyRelease>", lambda _: self.atualizar_valor_final())
        self.desconto_atendimento.bind("<KeyRelease>", lambda _: self.atualizar_valor_final())
        ctk.CTkLabel(self.aba_atendimento, text="Materiais deste atendimento — ajuste as quantidades usadas").pack(anchor="w", padx=22, pady=(8, 4))
        self.resumo_materiais = ctk.CTkScrollableFrame(self.aba_atendimento, height=175, width=550)
        self.resumo_materiais.pack(anchor="w", padx=22, pady=(0, 12))
        self.campos_consumo = {}
        self.escolha_servico.configure(command=lambda _: self.mostrar_materiais())
        ctk.CTkButton(self.aba_atendimento, text="Salvar atendimento e baixar estoque", height=42,
                      command=self.salvar_atendimento).pack(anchor="w", padx=22)

    def montar_produtos(self):
        ctk.CTkLabel(self.aba_produtos, text="Cadastrar produto", font=ctk.CTkFont(size=20, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(16, 0))
        linha = ctk.CTkFrame(self.aba_produtos, fg_color="transparent")
        linha.pack(fill="x", padx=10)
        self.produto_nome = self.campo(linha, "Produto", "Ex.: Luva")
        self.produto_estoque = self.campo(linha, "Estoque inicial", "Ex.: 20")
        self.produto_unidade = self.campo(linha, "Unidade", "un, caixa, ml...")
        ctk.CTkButton(self.aba_produtos, text="Cadastrar produto", command=self.cadastrar_produto)\
            .pack(anchor="w", padx=22, pady=14)
        ctk.CTkLabel(self.aba_produtos, text="Estoque atual (somente leitura)").pack(anchor="w", padx=22, pady=(8, 0))
        self.lista_produtos = ctk.CTkScrollableFrame(self.aba_produtos, height=180)
        self.lista_produtos.pack(fill="both", expand=True, padx=22, pady=8)
        linha2 = ctk.CTkFrame(self.aba_produtos, fg_color="transparent")
        linha2.pack(anchor="w", padx=22, pady=4)
        self.produto_repor = ctk.CTkComboBox(linha2, values=["Nenhum produto"], width=260)
        self.produto_repor.pack(side="left", padx=(0, 8))
        self.qtd_repor = ctk.CTkEntry(linha2, placeholder_text="Ajuste (+ entra / - sai)", width=190)
        self.qtd_repor.pack(side="left", padx=(0, 8))
        ctk.CTkButton(linha2, text="Ajustar estoque", command=self.repor_estoque).pack(side="left")

    def montar_servicos(self):
        ctk.CTkLabel(self.aba_servicos, text="Cadastrar serviço", font=ctk.CTkFont(size=20, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(20, 4))
        self.servico_nome = self.campo(self.aba_servicos, "Nome", "Ex.: Podoprofilaxia")
        self.servico_preco = self.campo(self.aba_servicos, "Preço (R$)", "Ex.: 80,00")
        ctk.CTkButton(self.aba_servicos, text="Cadastrar serviço", command=self.cadastrar_servico)\
            .pack(anchor="w", padx=22, pady=18)
        self.lista_servicos = ctk.CTkTextbox(self.aba_servicos, height=260)
        self.lista_servicos.pack(fill="both", expand=True, padx=22, pady=8)

    def montar_composicao(self):
        ctk.CTkLabel(self.aba_composicao, text="Materiais usados em cada serviço", font=ctk.CTkFont(size=20, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(20, 4))
        ctk.CTkLabel(self.aba_composicao, text="Informe quanto de cada produto é consumido por atendimento.")\
            .pack(anchor="w", padx=22, pady=5)
        self.comp_servico = ctk.CTkComboBox(self.aba_composicao, values=["Cadastre um serviço primeiro"], width=360)
        self.comp_servico.pack(anchor="w", padx=22, pady=(16, 8))
        self.comp_produto = ctk.CTkComboBox(self.aba_composicao, values=["Cadastre um produto primeiro"], width=360)
        self.comp_produto.pack(anchor="w", padx=22, pady=8)
        self.comp_quantidade = ctk.CTkEntry(self.aba_composicao, placeholder_text="Quantidade consumida", width=360)
        self.comp_quantidade.pack(anchor="w", padx=22, pady=8)
        ctk.CTkButton(self.aba_composicao, text="Adicionar / atualizar material", command=self.salvar_composicao)\
            .pack(anchor="w", padx=22, pady=8)
        self.lista_composicao = ctk.CTkTextbox(self.aba_composicao, height=180)
        self.lista_composicao.pack(fill="both", expand=True, padx=22, pady=12)
        self.comp_servico.configure(command=lambda _: self.mostrar_composicao())

    def montar_historico(self):
        ctk.CTkLabel(self.aba_historico, text="Histórico detalhado de atendimentos", font=ctk.CTkFont(size=20, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(20, 8))
        linha = ctk.CTkFrame(self.aba_historico, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=(0, 8))
        ctk.CTkLabel(linha, text="De").pack(side="left", padx=4)
        hoje = date.today()
        self.hist_inicio = ctk.CTkEntry(linha, width=120, placeholder_text="DD/MM/AAAA")
        self.hist_inicio.pack(side="left", padx=4)
        self.hist_inicio.insert(0, hoje.replace(day=1).strftime("%d/%m/%Y"))
        ctk.CTkLabel(linha, text="Até").pack(side="left", padx=4)
        self.hist_fim = ctk.CTkEntry(linha, width=120, placeholder_text="DD/MM/AAAA")
        self.hist_fim.pack(side="left", padx=4)
        self.hist_fim.insert(0, hoje.strftime("%d/%m/%Y"))
        ctk.CTkButton(linha, text="Mostrar histórico", command=self.atualizar_historico).pack(side="left", padx=8)
        linha_modo = ctk.CTkFrame(self.aba_historico, fg_color="transparent")
        linha_modo.pack(anchor="w", padx=22, pady=(0, 4))
        ctk.CTkLabel(linha_modo, text="Visualizar:").pack(side="left", padx=(0, 8))
        self.modo_historico = ctk.CTkSegmentedButton(
            linha_modo,
            values=["Por tipo de atendimento", "Por atendimento"],
            command=lambda _: self.atualizar_historico(),
        )
        self.modo_historico.pack(side="left")
        self.modo_historico.set("Por tipo de atendimento")
        self.lista_historico = ctk.CTkTextbox(self.aba_historico)
        self.lista_historico.pack(fill="both", expand=True, padx=22, pady=8)

    def montar_balanco(self):
        ctk.CTkLabel(self.aba_balanco, text="Balanço de recebimentos", font=ctk.CTkFont(size=20, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(20, 8))
        agora = date.today()
        primeiro_mes = agora.replace(day=1).isoformat()
        linha = ctk.CTkFrame(self.aba_balanco, fg_color="transparent")
        linha.pack(anchor="w", padx=18, pady=8)
        ctk.CTkLabel(linha, text="De (AAAA-MM-DD)").pack(side="left", padx=4)
        self.data_inicio = ctk.CTkEntry(linha, width=130, placeholder_text="DD/MM/AAAA")
        self.data_inicio.pack(side="left", padx=4)
        self.data_inicio.insert(0, agora.replace(day=1).strftime("%d/%m/%Y"))
        ctk.CTkLabel(linha, text="Até").pack(side="left", padx=4)
        self.data_fim = ctk.CTkEntry(linha, width=130, placeholder_text="DD/MM/AAAA")
        self.data_fim.pack(side="left", padx=4)
        self.data_fim.insert(0, agora.strftime("%d/%m/%Y"))
        ctk.CTkButton(linha, text="Mostrar balanço", command=self.atualizar_balanco).pack(side="left", padx=8)
        self.total_balanco = ctk.CTkLabel(self.aba_balanco, text="")
        self.total_balanco.pack(anchor="w", padx=22, pady=6)
        self.lista_balanco = ctk.CTkTextbox(self.aba_balanco)
        self.lista_balanco.pack(fill="both", expand=True, padx=22, pady=8)

    @staticmethod
    def numero(texto: str) -> float:
        return float(texto.strip().replace(",", "."))

    @staticmethod
    def selecionar(combo, itens, placeholder: str):
        if not itens:
            combo.configure(values=[placeholder])
            combo.set(placeholder)
            return
        nomes = [i["nome"] for i in itens]
        combo.configure(values=nomes)
        if combo.get() not in nomes:
            combo.set(nomes[0])

    def obter_por_nome(self, itens, nome: str):
        return next((item for item in itens if item["nome"] == nome), None)

    def atualizar_telas(self):
        produtos, servicos = self.banco.produtos(), self.banco.servicos()
        self.selecionar(self.produto_repor, produtos, "Nenhum produto")
        self.selecionar(self.comp_produto, produtos, "Cadastre um produto primeiro")
        self.selecionar(self.escolha_servico, servicos, "Cadastre um serviço primeiro")
        self.selecionar(self.comp_servico, servicos, "Cadastre um serviço primeiro")
        for widget in self.lista_produtos.winfo_children():
            widget.destroy()
        for p in produtos:
            linha = ctk.CTkFrame(self.lista_produtos, fg_color="transparent")
            linha.pack(fill="x", padx=4, pady=3)
            ctk.CTkLabel(linha, text=p["nome"], width=240, anchor="w").pack(side="left", padx=5)
            campo_estoque = ctk.CTkEntry(linha, width=120)
            campo_estoque.pack(side="left", padx=5)
            campo_estoque.insert(0, f"{p['estoque']:g}")
            campo_estoque.configure(state="disabled")
            ctk.CTkLabel(linha, text=p["unidade"]).pack(side="left", padx=4)
        self.lista_servicos.delete("1.0", "end")
        for s in servicos:
            self.lista_servicos.insert("end", f"{s['nome']} — R$ {s['preco']:.2f}\n")
        self.mostrar_materiais()
        self.mostrar_composicao()
        self.atualizar_historico()
        self.atualizar_balanco()

    def cadastrar_produto(self):
        try:
            nome, unidade = self.produto_nome.get().strip(), self.produto_unidade.get().strip() or "un"
            estoque = self.numero(self.produto_estoque.get())
            if not nome or estoque < 0:
                raise ValueError("Informe um nome e um estoque igual ou maior que zero.")
            self.banco.salvar_produto(nome, estoque, unidade)
            self.produto_nome.delete(0, "end"); self.produto_estoque.delete(0, "end"); self.produto_unidade.delete(0, "end")
            self.atualizar_telas()
        except (ValueError, sqlite3.IntegrityError) as e:
            messagebox.showerror("Não foi possível cadastrar", str(e) or "Esse produto já existe.")

    def repor_estoque(self):
        try:
            produto = self.obter_por_nome(self.banco.produtos(), self.produto_repor.get())
            qtd = self.numero(self.qtd_repor.get())
            if produto is None or qtd == 0:
                raise ValueError("Escolha um produto e informe um ajuste diferente de zero.")
            if produto["estoque"] + qtd < 0:
                raise ValueError("Não há essa quantidade em estoque.")
            self.banco.atualizar_estoque(produto["id"], qtd)
            self.qtd_repor.delete(0, "end"); self.atualizar_telas()
        except ValueError as e:
            messagebox.showerror("Verifique os dados", str(e))

    def cadastrar_servico(self):
        try:
            nome, preco = self.servico_nome.get().strip(), self.numero(self.servico_preco.get())
            if not nome or preco < 0:
                raise ValueError("Informe um nome e um preço igual ou maior que zero.")
            self.banco.salvar_servico(nome, preco)
            self.servico_nome.delete(0, "end"); self.servico_preco.delete(0, "end"); self.atualizar_telas()
        except (ValueError, sqlite3.IntegrityError) as e:
            messagebox.showerror("Não foi possível cadastrar", str(e) or "Esse serviço já existe.")

    def salvar_composicao(self):
        try:
            servico = self.obter_por_nome(self.banco.servicos(), self.comp_servico.get())
            produto = self.obter_por_nome(self.banco.produtos(), self.comp_produto.get())
            qtd = self.numero(self.comp_quantidade.get())
            if servico is None or produto is None or qtd <= 0:
                raise ValueError("Escolha um serviço, um produto e informe uma quantidade positiva.")
            self.banco.salvar_composicao(servico["id"], produto["id"], qtd)
            self.comp_quantidade.delete(0, "end"); self.mostrar_composicao(); self.mostrar_materiais()
        except (ValueError, sqlite3.IntegrityError) as e:
            messagebox.showerror("Verifique os dados", str(e))

    def mostrar_composicao(self):
        if not hasattr(self, "lista_composicao"):
            return
        self.lista_composicao.delete("1.0", "end")
        servico = self.obter_por_nome(self.banco.servicos(), self.comp_servico.get())
        if servico:
            for m in self.banco.composicao(servico["id"]):
                self.lista_composicao.insert("end", f"{m['nome']}: {m['quantidade']:g} {m['unidade']} por atendimento\n")

    def mostrar_materiais(self):
        if not hasattr(self, "resumo_materiais"):
            return
        for widget in self.resumo_materiais.winfo_children():
            widget.destroy()
        self.campos_consumo = {}
        servico = self.obter_por_nome(self.banco.servicos(), self.escolha_servico.get())
        if not servico:
            self.valor_atendimento.delete(0, "end")
            self.valor_final_label.configure(text="Valor final: R$ 0,00")
            ctk.CTkLabel(self.resumo_materiais, text="Cadastre um serviço para começar.").pack(anchor="w", padx=8, pady=6)
            return
        materiais = self.banco.composicao(servico["id"])
        self.valor_atendimento.delete(0, "end")
        self.valor_atendimento.insert(0, f"{servico['preco']:.2f}")
        self.atualizar_valor_final()
        ctk.CTkLabel(self.resumo_materiais, text="O valor pode ser ajustado para este atendimento.").pack(anchor="w", padx=8, pady=(4, 8))
        if not materiais:
            ctk.CTkLabel(self.resumo_materiais, text="Nenhum material vinculado. Configure na aba Materiais por serviço.").pack(anchor="w", padx=8)
        for m in materiais:
            produto = next(p for p in self.banco.produtos() if p["nome"] == m["nome"])
            linha = ctk.CTkFrame(self.resumo_materiais, fg_color="transparent")
            linha.pack(fill="x", padx=4, pady=3)
            ctk.CTkLabel(linha, text=f"{m['nome']} ({m['unidade']})", width=240, anchor="w").pack(side="left", padx=5)
            entrada = ctk.CTkEntry(linha, width=100)
            entrada.pack(side="left", padx=5)
            entrada.insert(0, f"{m['quantidade']:g}")
            ctk.CTkLabel(linha, text=f"padrão: {m['quantidade']:g}").pack(side="left", padx=5)
            self.campos_consumo[produto["id"]] = (entrada, m["nome"])

    def salvar_atendimento(self):
        cliente = self.cliente.get().strip()
        servico = self.obter_por_nome(self.banco.servicos(), self.escolha_servico.get())
        if not cliente or servico is None:
            messagebox.showerror("Dados incompletos", "Informe o nome da cliente e selecione um serviço.")
            return
        try:
            valor = self.numero(self.valor_atendimento.get())
            desconto = self.numero(self.desconto_atendimento.get() or "0")
            if valor < 0 or not 0 <= desconto <= 100:
                raise ValueError("Informe um valor não negativo e um desconto entre 0% e 100%.")
            preco_final = round(valor * (1 - desconto / 100), 2)
            consumos = {}
            for produto_id, (entrada, nome) in self.campos_consumo.items():
                qtd = self.numero(entrada.get())
                if qtd < 0:
                    raise ValueError(f"A quantidade de {nome} não pode ser negativa.")
                consumos[produto_id] = qtd
            self.banco.registrar_atendimento(cliente, servico["id"], consumos, preco_final)
            messagebox.showinfo("Atendimento salvo", "Atendimento registrado e estoque atualizado.")
            self.cliente.delete(0, "end")
            self.desconto_atendimento.delete(0, "end"); self.desconto_atendimento.insert(0, "0")
            self.atualizar_telas()
        except (ValueError, sqlite3.Error) as e:
            messagebox.showerror("Atendimento não salvo", str(e))

    def atualizar_historico(self):
        if not hasattr(self, "lista_historico"):
            return
        try:
            inicio = self.ler_data(self.hist_inicio.get())
            fim = self.ler_data(self.hist_fim.get())
            if inicio > fim:
                raise ValueError("A data inicial deve ser anterior ou igual à data final.")
            linhas = self.banco.relatorio_historico(inicio.isoformat(), fim.isoformat())
            atendimentos = {}
            resumo_servicos = {}
            resumo_materiais = {}
            ids_por_servico = {}
            for linha in linhas:
                aid = linha["id"]
                if aid not in atendimentos:
                    atendimentos[aid] = {
                        "data": linha["data"], "cliente": linha["cliente"],
                        "servico": linha["servico"], "preco": linha["preco"], "materiais": []
                    }
                if linha["nome_produto"] is not None:
                    material = (linha["nome_produto"], linha["unidade"], linha["quantidade"])
                    atendimentos[aid]["materiais"].append(material)
                    chave = (linha["nome_produto"], linha["unidade"])
                    resumo_materiais[chave] = resumo_materiais.get(chave, 0) + linha["quantidade"]
                resumo = resumo_servicos.setdefault(linha["servico"], {"quantidade": 0, "total": 0.0, "materiais": {}})
                ids_servico = ids_por_servico.setdefault(linha["servico"], set())
                if aid not in ids_servico:
                    resumo["quantidade"] += 1
                    resumo["total"] += linha["preco"]
                    ids_servico.add(aid)
                if linha["nome_produto"] is not None:
                    resumo["materiais"][chave] = resumo["materiais"].get(chave, 0) + linha["quantidade"]
            self.lista_historico.delete("1.0", "end")
            self.lista_historico.insert("end", f"HISTÓRICO: {inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}\n")
            if not atendimentos:
                self.lista_historico.insert("end", "\nNenhum atendimento encontrado nesse período.\n")
                return
            total_valor = sum(a["preco"] for a in atendimentos.values())
            if self.modo_historico.get() == "Por tipo de atendimento":
                self.lista_historico.insert("end", "\nTOTAIS POR TIPO DE ATENDIMENTO\n")
                for nome, resumo in resumo_servicos.items():
                    self.lista_historico.insert("end", f"\n{nome}\n")
                    self.lista_historico.insert("end", f"  Quantidade de atendimentos: {resumo['quantidade']}\n")
                    self.lista_historico.insert("end", f"  Valor total recebido: R$ {resumo['total']:.2f}\n")
                    materiais = resumo["materiais"]
                    texto_materiais = ", ".join(f"{n}: {q:g} {u}" for (n, u), q in materiais.items())
                    self.lista_historico.insert("end", f"  Materiais usados: {texto_materiais or 'nenhum registrado'}\n")
            else:
                self.lista_historico.insert("end", "\nATENDIMENTOS INDIVIDUAIS\n")
                for a in atendimentos.values():
                    try:
                        data_formatada = datetime.fromisoformat(a["data"]).strftime("%d/%m/%Y %H:%M")
                    except ValueError:
                        data_formatada = a["data"]
                    self.lista_historico.insert("end", f"\n{data_formatada} | {a['cliente']} | {a['servico']} | R$ {a['preco']:.2f}\n")
                    texto_materiais = ", ".join(f"{n}: {q:g} {u}" for n, u, q in a["materiais"])
                    self.lista_historico.insert("end", f"  Materiais: {texto_materiais or 'nenhum registrado'}\n")

            self.lista_historico.insert("end", "\nTOTAL DO PERÍODO\n")
            self.lista_historico.insert("end", f"Quantidade total de atendimentos: {len(atendimentos)}\nValor total recebido: R$ {total_valor:.2f}\n")
            if resumo_materiais:
                self.lista_historico.insert("end", "Materiais totais usados: " + ", ".join(
                    f"{n}: {q:g} {u}" for (n, u), q in resumo_materiais.items()) + "\n")
            else:
                self.lista_historico.insert("end", "Materiais totais usados: nenhum registrado\n")
        except ValueError as e:
            messagebox.showerror("Período inválido", str(e))

    @staticmethod
    def ler_data(valor: str) -> date:
        valor = valor.strip()
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(valor, formato).date()
            except ValueError:
                pass
        raise ValueError("Informe as datas como DD/MM/AAAA.")

    def atualizar_valor_final(self):
        if not hasattr(self, "valor_atendimento"):
            return
        try:
            valor = self.numero(self.valor_atendimento.get() or "0")
            desconto = self.numero(self.desconto_atendimento.get() or "0")
            if valor < 0 or not 0 <= desconto <= 100:
                raise ValueError
            final = round(valor * (1 - desconto / 100), 2)
            self.valor_final_label.configure(text=f"Valor final: R$ {final:.2f}")
        except ValueError:
            self.valor_final_label.configure(text="Confira o valor e o desconto (0% a 100%).")

    def atualizar_balanco(self):
        try:
            inicio = self.ler_data(self.data_inicio.get())
            fim = self.ler_data(self.data_fim.get())
            if inicio > fim:
                raise ValueError("A data inicial deve ser anterior ou igual à data final.")
            linhas = self.banco.balanco(inicio.isoformat(), fim.isoformat())
            total = sum(l["total"] or 0 for l in linhas)
            self.lista_balanco.delete("1.0", "end")
            self.lista_balanco.insert("end", f"Serviços recebidos entre {inicio.strftime('%d/%m/%Y')} e {fim.strftime('%d/%m/%Y')}:\n\n")
            for l in linhas:
                self.lista_balanco.insert("end", f"{l['servico']}: {l['quantidade']} atendimento(s) — R$ {l['total']:.2f}\n")
            if not linhas:
                self.lista_balanco.insert("end", "Nenhum atendimento nesse período.\n")
            self.total_balanco.configure(text=f"Total recebido no período: R$ {total:.2f}")
        except ValueError as e:
            messagebox.showerror("Período inválido", str(e))

    def fechar(self):
        self.banco.fechar()
        self.destroy()


if __name__ == "__main__":
    Aplicativo().mainloop()

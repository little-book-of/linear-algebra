# Makefile for Quarto multi-language book builds
#
# Usage:
#   make help
#   make release
#   make translate
#   make render-all

QUARTO       = quarto
QUARTO_VERSION ?= 1.6.43
QUARTO_DEB := quarto-$(QUARTO_VERSION)-linux-amd64.deb
QUARTO_DEB_URL := https://github.com/quarto-dev/quarto-cli/releases/download/v$(QUARTO_VERSION)/$(QUARTO_DEB)
PYTHON       = python3
RELEASE_DIR  = releases
BUILD_DIR    = docs
ZH_PROJECT   = books/zh-CN
VI_PROJECT   = books/vi
JA_PROJECT   = books/ja
KO_PROJECT   = books/ko
ES_PROJECT   = books/es
DE_PROJECT   = books/de

.PHONY: all install-quarto check-quarto preview render render-zh render-vi render-ja render-ko render-es render-de render-all translate translate-vi translate-ja translate-ko translate-es translate-de release clean help

# Default target
all: release ## Default: build and copy distributables

install-quarto: ## Install Quarto CLI (Linux .deb)
	@if command -v $(QUARTO) >/dev/null 2>&1; then \
		echo "Quarto already installed: $$($(QUARTO) --version)"; \
		exit 0; \
	fi
	@echo "Installing Quarto v$(QUARTO_VERSION)..."
	@curl -fsSL -o /tmp/$(QUARTO_DEB) $(QUARTO_DEB_URL)
	@dpkg -i /tmp/$(QUARTO_DEB) || apt-get install -f -y
	@rm -f /tmp/$(QUARTO_DEB)
	@echo "Installed: $$($(QUARTO) --version)"

check-quarto: ## Check Quarto version
	@$(QUARTO) --version

preview: ## Start Quarto live preview for English book
	$(QUARTO) preview

render: ## Render English site (HTML only)
	$(QUARTO) render --to html --no-execute

render-zh: ## Render Chinese website (HTML)
	$(QUARTO) render $(ZH_PROJECT) --no-execute

render-vi: ## Render Vietnamese website (HTML)
	$(QUARTO) render $(VI_PROJECT) --no-execute

render-ja: ## Render Japanese website (HTML)
	$(QUARTO) render $(JA_PROJECT) --no-execute

render-ko: ## Render Korean website (HTML)
	$(QUARTO) render $(KO_PROJECT) --no-execute

render-es: ## Render Spanish website (HTML)
	$(QUARTO) render $(ES_PROJECT) --no-execute

render-de: ## Render German website (HTML)
	$(QUARTO) render $(DE_PROJECT) --no-execute

render-all: render render-zh render-vi render-ja render-ko render-es render-de ## Render English + Chinese + Vietnamese + Japanese + Korean + Spanish + German sites

translate: ## Translate source book to Chinese under books/zh-CN/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_zh.py

translate-vi: ## Translate source book to Vietnamese under books/vi/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_vi.py

translate-ja: ## Translate source book to Japanese under books/ja/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_ja.py

translate-ko: ## Translate source book to Korean under books/ko/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_ko.py

translate-es: ## Translate source book to Spanish under books/es/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_es.py

translate-de: ## Translate source book to German under books/de/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_de.py

release: clean render-all ## Clean, render both sites, and copy distributables to releases/
	mkdir -p $(RELEASE_DIR)
	cp -f $(BUILD_DIR)/book-latex/book.tex $(RELEASE_DIR)/
	cp -f $(BUILD_DIR)/book.epub             $(RELEASE_DIR)/
	cp -f $(BUILD_DIR)/book.pdf              $(RELEASE_DIR)/

clean: ## Remove old build artifacts
	rm -rf $(BUILD_DIR)/* $(RELEASE_DIR)/*

help: ## Show this help (auto-discovered from targets)
	@printf "Targets:\n"
	@awk 'BEGIN {FS":.*##"} \
		/^[a-zA-Z0-9_.-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST) | sort

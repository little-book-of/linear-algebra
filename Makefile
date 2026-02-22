# Makefile for Quarto multi-language book builds
#
# Usage:
#   make help
#   make release
#   make translate
#   make render-all

QUARTO       = quarto
PYTHON       = python3
RELEASE_DIR  = releases
BUILD_DIR    = docs
ZH_PROJECT   = books/zh-CN
VI_PROJECT   = books/vi
JA_PROJECT   = books/ja

.PHONY: all preview render render-zh render-vi render-ja render-all translate translate-vi translate-ja release clean help

# Default target
all: release ## Default: build and copy distributables

preview: ## Start Quarto live preview for English book
	$(QUARTO) preview

render: ## Render English book/site (HTML/PDF/EPUB/TeX as configured)
	$(QUARTO) render

render-zh: ## Render Chinese website (HTML)
	$(QUARTO) render $(ZH_PROJECT)

render-vi: ## Render Vietnamese website (HTML)
	$(QUARTO) render $(VI_PROJECT)

render-ja: ## Render Japanese website (HTML)
	$(QUARTO) render $(JA_PROJECT)

render-all: render render-zh render-vi render-ja ## Render English + Chinese + Vietnamese + Japanese sites

translate: ## Translate source book to Chinese under books/zh-CN/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_zh.py

translate-vi: ## Translate source book to Vietnamese under books/vi/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_vi.py

translate-ja: ## Translate source book to Japanese under books/ja/
	$(PYTHON) -m pip install -r scripts/requirements.txt
	$(PYTHON) scripts/translate_to_ja.py

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

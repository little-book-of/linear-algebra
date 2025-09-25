# Makefile for Quarto book builds
#
# Usage:
#   make help
#   make release
#   make preview
#   make clean

QUARTO      = quarto
RELEASE_DIR = releases
BUILD_DIR   = docs

.PHONY: all preview render release clean help

# Default target
all: release ## Default: build and copy distributables

preview: ## Start Quarto live preview
	$(QUARTO) preview

render: ## Render all formats with Quarto (HTML/PDF/EPUB/TeX as configured)
	$(QUARTO) render

release: clean render ## Clean, render, and copy distributables to releases/
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

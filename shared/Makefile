# Invoked by `sam build` for the SharedLayer resource (see the
# BuildMethod: makefile / ContentUri: shared/ in template.yaml). SAM cd's
# into this directory and runs `make build-SharedLayer`, expecting the
# layer's contents to land in $(ARTIFACTS_DIR).
#
# Lambda layers need Python code under a python/ prefix so it lands on
# sys.path — that's purely a packaging detail of the build output, so it's
# generated here rather than kept as a second, hand-synced copy in git.
build-SharedLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python/shared"
	cp *.py "$(ARTIFACTS_DIR)/python/shared/"

.PHONY: test check build
VERSION ?= 1.0.0-phase02

test:
	go test -race ./...

check:
	@test -z "$$(gofmt -l cmd internal)"
	go vet ./...

build:
	mkdir -p bin
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w -X main.version=$(VERSION)' -o bin/monitor-agent-linux-amd64 ./cmd/monitor-agent
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags='-s -w -X main.version=$(VERSION)' -o bin/monitor-agent-linux-arm64 ./cmd/monitor-agent

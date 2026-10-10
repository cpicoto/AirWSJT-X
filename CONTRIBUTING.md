# Contributing to AirWSJT-X

Thanks for your interest in contributing to AirWSJT-X! This project is built by and for the amateur radio community.

## Getting Started

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-improvement`)
3. Make your changes
4. Run the tests (`python -m unittest discover -s tests -v`)
   - Cross-platform tests live in `tests/test_cross_platform.py` and cover path handling, atomic writes, struct endianness, floating-point consistency, and callsign case normalisation. CI runs this suite on Ubuntu, Windows, and macOS.
5. Commit your changes (`git commit -m 'Add my improvement'`)
6. Push to the branch (`git push origin feature/my-improvement`)
7. Open a Pull Request

## Development Setup

See [README.md](README.md#building-from-source) for build instructions.

## Guidelines

- **Keep it focused.** Each PR should address one thing — a bug fix, a feature, or a refactor. Not all three.
- **Test your changes.** Add or update tests for new functionality. Run the full test suite before submitting.
- **Match the existing style.** Follow the conventions already in the codebase for naming, formatting, and structure.
- **Document user-facing changes.** Update the README if your change affects how users interact with the app.

## Reporting Bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your platform (OS, WSJT-X version, rotator hardware if relevant)

## Suggesting Features

Open an issue describing the use case. For aircraft scatter and microwave operation, context about your station setup and operating scenario is helpful.

## License

By contributing, you agree that your contributions will be licensed under the [GNU General Public License v3.0](LICENSE).

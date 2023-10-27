# Contributing to runpod-python

Thank you for your interest in contributing to the runpod-python project! This document is a set of guidelines for contributing to this project on GitHub. These are guidelines, not rules. This guide is meant to make it easy for you to get involved.

## Reporting Bugs and Suggesting Enhancements

If you have found a bug or would like to suggest a new feature or enhancement, please start by checking if there is an existing [issue](https://github.com/runpod/runpod-python/issues) for it. If there isn't one, you can create one, clearly describing the bug or feature, with as much detail as possible.

## Code Contributions

Here is a quick guide on how to contribute code to this project:

1. Fork the repository and create your branch from `main`.

2. Clone the forked repository to your machine.

    ```bash
    git clone https://github.com/<your-username>/runpod-python.git
    ```

3. Navigate to your local repository.

    ```bash
    cd runpod-python
    ```

4. Create a branch for your edits.

    ```bash
    git checkout -b name-of-your-branch
    ```

5. Make your changes in the new branch.

6. Run tests to ensure that your changes do not break any existing functionality. You can run tests using the following command:

    ```bash
    pip install .[test]
    pytest
    ```

7. Commit your changes, providing descriptive commit messages.

    ```bash
    git commit -am "Your descriptive commit message"
    ```

8. Push your changes to your GitHub account.

    ```bash
    git push -u origin name-of-your-branch
    ```

9. Open a Pull Request against the main branch of this repository.

Please note that the project maintainers will review your changes. They might ask for changes before your Pull Request gets merged. Coding is a group activity, and collaboration is key!

### Code Style

This project adheres to the [PEP 8](https://www.python.org/dev/peps/pep-0008/) code style. We use [Black](https://black.readthedocs.io/en/stable/) to format our code. To ensure your code fits with the style, run Black on your code before committing.

### Testing

Before submitting a pull request, please make sure your changes pass all tests. New features should include additional unit tests validating the changes.

## Getting Help

If you have any questions or need help with contributing, feel free to reach out on the [issue tracker](https://github.com/runpod/runpod-python/issues) or open a new issue. We're here to help!

Thank you for contributing!

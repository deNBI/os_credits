OpenStack Credits Service
=========================

This service is the main component of the *de.NBI* billing system and fulfills the
following tasks:

* Process incoming usage measurements and bill projects accordingly
* Send notifications in case of events such as a low number of remaining credits
* Provide a history of the credits of a project

The service is integrated into the *Portal stack* of the `project_usage project
<https://github.com/deNBI/project_usage>`_, please refer to its wiki for corresponding
setup instructions/required services.

The development has been part of the master thesis **INSERT_TITLE_HERE** which therefore
contains a large introduction to the area of *Cloud Billing* and motivations which lead
to the current design. An operation and development manual can be found inside the
``docs/`` folder of this repository which can be build via ``make docs``.

Development
----------

The project has been developed with Python 3.7 and uses the `aiohttp
<https://docs.aiohttp.org>`_ framework communication. Its dependencies are managed via
`Poetry <https://pypi.org/project/poetry/>`_, so you’ll have to install it and then
execute ``poetry install`` to get up and running. Your first action should be ``poetry
run pre-commit install`` to install `Pre-Commit Hooks <https://pre-commit.com/>`_, to
make sure that every of your commits results in a well-tested application.

Monitoring/Debugging
~~~~~~~~~~~~~~~~~~~~

If the application misbehaves and you would like to set a lower log
level or get stats **without restarting** it you have two possibilities:

1. Use the ``/logconfig`` endpoint to change the logging settings of the
   running application.
2. Query the ``/stats`` endpoint, optionally with ``?verbose=true``

Building
--------

Use the provided ``Makefile`` via ``make build-docker``. This will build
``$USER/os_credits`` and use the version of the project as version of
the image. To modify this values call
``make build-docker DOCKER_USERNAME=<your_username>``.


Stack integration
~~~~~~~~~~~~~~~~~

To run the code use the provided ``Dockerfile.dev`` which you can build
via ``make docker-build-dev``. Afterward use ``make docker-run`` to
integrate the development container into the ``project_usage`` stack.

The development container is using the ``adev runserver`` command from
the
```aiohttp-devtools`` <https://github.com/aio-libs/aiohttp-devtools>`__
which will restart your app on any code change. But since the code is
bind mounted inside the container you can simply continue editing and
have it restart on any change.

Tests
~~~~~

Tests are written against ```pytest`` <https://pytest.org>`__ and can be
executed from the project directory via ``make test``.

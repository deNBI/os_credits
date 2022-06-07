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

Development
-----------

The project has been developed with Python 3.7 and uses the `aiohttp
<https://docs.aiohttp.org>`_ framework communication. Its dependencies are managed via
`Poetry <https://pypi.org/project/poetry/>`_.
If you want to develop while using the whole stack, please see `project_usage project
<https://github.com/deNBI/project_usage>`_ for more information.
If you only need some endpoints which do not need the whole stack (e.g. /cost_per_hour),
copy the .default.env to .env and run make up-dev. This will build the container from
Dockerfile.dev. Please note that a named volume will be created: credits_data.

Monitoring/Debugging
~~~~~~~~~~~~~~~~~~~~

If the application misbehaves and you would like to set a lower log
level or get stats **without restarting** it you have two possibilities:

1. Use the ``/logconfig`` endpoint to change the logging settings of the
   running application.
2. Query the ``/stats`` endpoint, optionally with ``?verbose=true``

Building
--------

Use the provided ``Makefile`` via ``make docker-build``. This will build
``$USER/os_credits`` and use the version of the project as version of
the image. To modify this values call
``make build-docker DOCKER_USERNAME=<your_username> DOCKER_IMAGENAME=<your_imagename>``.


Additional notes
~~~~~~~~~~~~~~~~~

The development has been part of the master thesis **Accounting and Reporting of
OpenStack Cloud instances via Prometheus** which therefore
contains a large introduction to the area of *Cloud Billing* and motivations which lead
to the current design.

Update 2022:
The design of this system changed due to exchanging InfluxDB with TimescaleDB
and some unforeseen requirements.

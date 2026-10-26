FROM astrocrpublic.azurecr.io/runtime:3.3-7

COPY --chown=astro:0 pagination_pkg /tmp/pagination_pkg
RUN pip install --no-cache-dir /tmp/pagination_pkg

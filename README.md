# Hamburg Open Data Pipeline

RDF-Connect pipeline to produce a knowledge graph from the traffic counting datastreams from the Hamburg Open Data Portal.


## Setting up the graphstore
You can use the following docker compose file to setup an oxigraph instance

First, create a shared network to use with your graphstore instance and your rdfc pipeline
```bash
docker network create rdfc-net
```

```yaml
services:
  oxigraph:
    image: ghcr.io/oxigraph/oxigraph:latest
    container_name: oxigraph
    command: serve --location /data --bind 0.0.0.0:7878
    ports:
      - "7878:7878"
    volumes:
      - ./oxigraph-data:/data
    restart: unless-stopped
    networks:
      - rdfc-net
networks:
  rdfc-net:
    external: true
```

## Setting up the docker

**If you do not want to install these tools locally**, we have provided a **Dockerfile** that sets up an environment with all software installed.
You can build and run it as follows.

First, start the docker environment

```bash
# Start the Docker Compose environment containing the devbox and Virtuoso
cd pipeline/resources
docker compose up -d
```

Next, we access the docker container
```bash
# Access the devbox container
docker compose exec devbox bash
```

Then we initialize the dependencies inside the container
```bash
# Run the following commands inside the devbox to install all dependencies
cd pipeline/
npm install
gradle copyPlugins
hatch env create
hatch shell
```

Then, we run the pipeline inside the container
```bash
# Run the pipeline inside the devbox
npx rdfc pipeline.ttl
```
Or, run the pipeline in debug mode
```bash
# Run the pipeline inside the devbox
DEBUG=* npx rdfc pipeline.ttl
```

## Pipeline setup
The current pipeline follows the following setup: 

### Sensorthings-api-fetcher-ts
The [sensorthings](https://github.com/rdf-connect/sensorthings-api-fetcher-ts) component retrieves a set of one or more datastreams according to the [Sensorthings API specification](https://docs.ogc.org/is/18-088/18-088.html). It performs an initial metadata retrieval step for each datastream that is processed, and embeds the observations in this metadata before forwarding it through the pipeline. The `follow` property makes registers the component to the provided MQTT broker for continuous retrieval of new updates to the processed datastreams.

### RML mapper
The [RML](https://github.com/rdf-connect/rml-processor-jvm) component performs the provided RML mapping over the input data retrieved from the sensorthings component.

### Translator
The [translator](./processor/translation-processor-py) component adds translations of all German literals in Dutch.

### Validator
The [validator](https://github.com/rdf-connect/shacl-processor-ts) component validates the output data according to the given shape. In this case, this is the [SHACL template for OSLO verkeersmetingen](https://data.vlaanderen.be/doc/applicatieprofiel/verkeersmetingen/erkendestandaard/2024-04-17/shacl/Verkeersmetingen-ap-SHACL.ttl).

### Ingest
The [ingest](https://github.com/rdf-connect/sparql-ingest-processor-ts) component ingests the resulting data into a graphstore via the SPARQL INSERT protocol.

## Setting target datastreams
The target datastreams are set directly in the [rdfc pipeline file](./pipeline/pipeline.ttl), which takes a set of datastream URLs.
```
# fetcher to get SensorThings API data
<fetcher> a rdfc:SensorThingsFetcher;
    rdfc:datastream <target>, <pipelines>, ...
```

## Mappings
The mapping file can be found [as an RML document](./pipeline/resources/mapping.rml.ttl), generated from the [YARRRML mapping](./pipeline/resources/yarrrml/mapping.yml).
When generating the RML file from an updated YARRRML file, note that you need to update the source to comply with the source defined in the pipeline, which is currently `http://example.org/source1`. An example of this can be found at the top of the used [RML mapping document](./pipeline/resources/mapping.rml.ttl). 

**Note:** The current mapping is incomplete with regards to the direction, and needs a translation from the coordinates and the cardinal directions of the measured traffic counts in the output literals to a concrete Wegsegment + Rijrichting combo to comply with the OSLO Verkeersmetingen standard.
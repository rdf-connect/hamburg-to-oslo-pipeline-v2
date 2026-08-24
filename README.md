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

### Road snapper
The [road-snapper](./processor/road-snapper-py) component is a **pre-RML JSON enricher**. The Hamburg source gives each measurement point only a single coordinate — no road/segment — but OSLO Verkeersmetingen requires a `Wegsegment`. This component snaps each record's coordinate to the nearest drivable road in an OpenStreetMap extract and injects a `_wegsegment` object (centerline + begin/end node geometry and the snap offset) into the SensorThings JSON, so the RML mapper can build the `Wegsegment` from it. See its [README](./processor/road-snapper-py/README.md) for the required OSM data.

### Direction mapper
The [direction-mapper](./processor/direction-mapper-py) component is the second **pre-RML JSON enricher**. It reads each record's German direction phrase (e.g. `"Nord nach Süd"`), classifies it against the snapped segment's bearing as an INSPIRE `LinkDirectionValue` (`inDirection` / `inOppositeDirection` / `bothDirections`), and injects a `_rijrichting` object into the JSON. This is what lets the mapping emit a `Rijrichting` relative to the `Wegsegment`.

### RML mapper
The [RML](https://github.com/rdf-connect/rml-processor-jvm) component performs the provided RML mapping over the input data retrieved from the sensorthings component.

### Translator
The [translator](./processor/translation-processor-py) component adds translations of all German literals in Dutch.

### Validator
The [validator](https://github.com/rdf-connect/shacl-processor-ts) component validates the output data according to the given shape. In this case, this is the [SHACL template for OSLO verkeersmetingen](https://data.vlaanderen.be/doc/applicatieprofiel/verkeersmetingen/erkendestandaard/2024-04-17/shacl/Verkeersmetingen-ap-SHACL.ttl).

### Poster
The [http-utils](./processor/http-utils-processor-ts) component posts the validated, conformant data into the graphstore via the SPARQL Graph Store HTTP protocol (a `POST` to oxigraph's `/store` endpoint for the target named graph).

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

The direction modelling — deriving a concrete `Wegsegment` + `Rijrichting` from the raw coordinate and the German direction phrase — is handled by the [road-snapper](./processor/road-snapper-py) and [direction-mapper](./processor/direction-mapper-py) enrichers described above, which inject the needed `_wegsegment` and `_rijrichting` fields into the JSON before the mapping runs. The mapping and SHACL shape are aligned with the [Telraam LDES reference](./pipeline/resources) so the output is OSLO-conformant; a validated example record is in [pipeline/sample-record.ttl](./pipeline/sample-record.ttl).
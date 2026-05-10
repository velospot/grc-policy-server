- add Opik for llm observability and tracing.

[ Web UI / API Clients ]
          |
      [API Gateway]
          |
  -------------------------
  |           |           |
[Auth]   [Core API]   [Admin API]
              |
   ------------------------------------------
   |            |            |               |
[Ingestion] [Doc Intel] [Comparison]   [Chat/Reasoning]
   |            |            |               |
   ------------------------------------------
              |
      [Knowledge / Evidence Layer]
              |
 ----------------------------------------------------
 |         |           |          |         |        |
[RDBMS] [Object Store] [Search] [Vector] [Graph] [Cache]
              |
        [Observability / Audit]

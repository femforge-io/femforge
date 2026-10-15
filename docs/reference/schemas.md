# JSON schemas

The JSON schemas of the persisted models, served at a stable URL for a `$schema` key.

```python exec="true"
from femforge.schemas import SCHEMA_MODELS

print("| Model | Schema | Defined in |")
print("| --- | --- | --- |")
for name, model in SCHEMA_MODELS.items():
    print(f"| `{name}` | [{name}.json](/schema/{name}.json) | `{model.__module__}` |")
```

::: femforge.schemas

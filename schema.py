# The gateway has already expanded query aliases and fragments.
# True means GraphQL NON_NULL; the next entry is the enclosing value.
NULL_CHAIN = {
    "value": [("reading", True), ("readings", True), ("station", False)],
    "code": [("station", False)],
}
COLUMNS = ["station", "rid", "value", "quality"]

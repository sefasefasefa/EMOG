"""
Database Isolation API Views
REST endpoints for safe database query execution.
"""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .db_isolation import get_db_isolation, QueryResult


@csrf_exempt
@require_http_methods(["POST"])
def db_query(request):
    """
    Execute a read-only database query.
    
    POST /sandbox/db/query/
    {
        "query": "SELECT * FROM users WHERE id = ?",
        "params": [1],
        "allow_write": false
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    query = data.get("query", "").strip()
    params = data.get("params", [])
    allow_write = data.get("allow_write", False)
    
    if not query:
        return JsonResponse({"ok": False, "error": "Query required"}, status=400)
    
    isolation = get_db_isolation()
    
    try:
        result = isolation.execute_safe(query, tuple(params), allow_write=allow_write)
        
        return JsonResponse({
            "ok": True,
            "result": {
                "columns": result.columns,
                "rows": result.rows,
                "rowcount": result.rowcount,
                "execution_time_ms": result.execution_time_ms,
            }
        })
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Query failed: {str(e)}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def db_transaction(request):
    """
    Execute multiple queries in a transaction.
    
    POST /sandbox/db/transaction/
    {
        "queries": [
            ["INSERT INTO users (name) VALUES (?)", ["John"]],
            ["SELECT * FROM users WHERE name = ?", ["John"]]
        ],
        "read_only": false
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    queries = data.get("queries", [])
    read_only = data.get("read_only", False)
    
    if not queries:
        return JsonResponse({"ok": False, "error": "Queries required"}, status=400)
    
    isolation = get_db_isolation()
    
    try:
        # Convert to list of tuples
        query_tuples = [(q[0], tuple(q[1])) if isinstance(q, list) and len(q) == 2 
                       else (q, ()) for q in queries]
        
        results = isolation.execute_in_transaction(query_tuples, read_only=read_only)
        
        return JsonResponse({
            "ok": True,
            "results": [
                {
                    "columns": r.columns,
                    "rows": r.rows,
                    "rowcount": r.rowcount,
                }
                for r in results
            ]
        })
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Transaction failed: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def db_schema(request):
    """
    Get database schema information.
    
    GET /sandbox/db/schema/
    """
    isolation = get_db_isolation()
    
    try:
        schema = isolation.get_schema()
        return JsonResponse({
            "ok": True,
            "schema": schema
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Schema retrieval failed: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def db_tables(request):
    """
    List all tables in the database.
    
    GET /sandbox/db/tables/
    """
    isolation = get_db_isolation()
    
    try:
        schema = isolation.get_schema()
        tables = [
            {
                "name": name,
                "columns": info["columns"],
                "create_sql": info["create_sql"]
            }
            for name, info in schema.items()
        ]
        return JsonResponse({
            "ok": True,
            "tables": tables
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
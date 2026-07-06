import os
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Xiyou GPT Action", version="3.0.0")

GPT_ACTION_KEY = "123456"

XIYOU_API_KEY = os.getenv("XIYOU_API_KEY")
XIYOU_BASE_URL = "https://openapi.xydc.com"


class AsinRequest(BaseModel):
    marketplace: str
    asin: str
    size: int = 10


def xiyou_headers():
    if not XIYOU_API_KEY:
        raise HTTPException(status_code=500, detail="Render 未设置 XIYOU_API_KEY")

    return {
        "X-Auth-Version": "2.0",
        "X-Api-Key": XIYOU_API_KEY,
        "Content-Type": "application/json"
    }


async def xiyou_post(path: str, payload: dict):
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            XIYOU_BASE_URL + path,
            headers=xiyou_headers(),
            json=payload
        )

    try:
        body = response.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "西柚返回的不是 JSON",
                "status_code": response.status_code,
                "text": response.text[:500]
            }
        )

    return {
        "status_code": response.status_code,
        "cost_credits": response.headers.get("X-Cost-Credits"),
        "trace_id": response.headers.get("X-Trace-Id"),
        "body": body
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "message": "服务器已上线",
        "xiyou_key_set": bool(XIYOU_API_KEY)
    }


@app.get("/privacy")
def privacy():
    return {
        "name": "Xiyou GPT Action",
        "privacy": "This service forwards ASIN and marketplace requests to Xiyou OpenAPI. It does not store user conversation content."
    }


@app.post("/asin/deep-dive")
async def asin_deep_dive(
    req: AsinRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    if x_api_key != GPT_ACTION_KEY:
        raise HTTPException(status_code=401, detail="API Key 错误")

    marketplace = req.marketplace.upper().strip()
    asin = req.asin.upper().strip()
    size = min(req.size or 10, 10)

    # 1. ASIN 商品信息：低成本接口
    info_raw = await xiyou_post("/v1/asins/info", {
        "entities": [
            {
                "country": marketplace,
                "asin": asin
            }
        ]
    })

    # 2. ASIN 反查关键词：限制10条，节省额度
    keywords_raw = await xiyou_post("/v1/asins/research/list/period", {
        "asin": asin,
        "country": marketplace,
        "page": 1,
        "pageSize": size,
        "period": "last7days",
        "sort": {
            "field": "advertisingTraffic",
            "order": "desc"
        }
    })

    info_body = info_raw.get("body")
    keywords_body = keywords_raw.get("body")

    if info_raw.get("status_code") >= 400:
        return {
            "ok": False,
            "source": "xiyou",
            "stage": "asin_info",
            "status_code": info_raw.get("status_code"),
            "trace_id": info_raw.get("trace_id"),
            "error": info_body
        }

    if keywords_raw.get("status_code") >= 400:
        return {
            "ok": False,
            "source": "xiyou",
            "stage": "asin_keywords",
            "status_code": keywords_raw.get("status_code"),
            "trace_id": keywords_raw.get("trace_id"),
            "error": keywords_body
        }

    entities = info_body.get("entities", []) if isinstance(info_body, dict) else []
    asin_info = entities[0] if entities else {}

    keyword_list = keywords_body.get("list", []) if isinstance(keywords_body, dict) else []

    keywords = []
    for item in keyword_list[:size]:
        traffic = (item.get("trafficSummary") or {}).get("traffic") or {}
        acquisition = (item.get("trafficSummary") or {}).get("trafficAcquisitionRate") or {}
        ranks = item.get("ranks") or []

        organic_rank = None
        ad_rank = None

        for rank in ranks:
            position = rank.get("position")
            if position == "or":
                organic_rank = rank.get("totalRank") or rank.get("pageRank")
            if position == "sp":
                ad_rank = rank.get("totalRank") or rank.get("pageRank")

        keywords.append({
            "keyword": item.get("searchTerm"),
            "total_traffic": traffic.get("total"),
            "organic_traffic": traffic.get("organic"),
            "advertising_traffic": traffic.get("advertising"),
            "traffic_acquisition_total": acquisition.get("total"),
            "traffic_acquisition_organic": acquisition.get("organic"),
            "traffic_acquisition_advertising": acquisition.get("advertising"),
            "organic_rank": organic_rank,
            "ad_rank": ad_rank
        })

    return {
        "ok": True,
        "source": "xiyou",
        "message": "已获取西柚真实 ASIN 商品信息和反查关键词数据",
        "query": {
            "marketplace": marketplace,
            "asin": asin,
            "size": size,
            "period": "last7days"
        },
        "usage": {
            "asin_info_cost_credits": info_raw.get("cost_credits"),
            "keyword_cost_credits": keywords_raw.get("cost_credits"),
            "asin_info_trace_id": info_raw.get("trace_id"),
            "keyword_trace_id": keywords_raw.get("trace_id")
        },
        "detail": {
            "asin": asin_info.get("asin") or asin,
            "marketplace": asin_info.get("country") or marketplace,
            "title": asin_info.get("title"),
            "amazon_url": asin_info.get("amazonUrl"),
            "image": asin_info.get("smallPicUrl"),
            "currency": asin_info.get("currency"),
            "price": asin_info.get("price"),
            "rating": asin_info.get("stars"),
            "reviews": asin_info.get("ratings"),
            "bsr": "数据缺失",
            "monthly_sales": "数据缺失",
            "monthly_revenue": "数据缺失"
        },
        "traffic_keywords": keywords,
        "data_gap": [
            "暂未接入 BSR 趋势",
            "暂未接入订单量趋势",
            "暂未接入 PPC 竞价",
            "暂未接入月度关键词反查"
        ]
    }

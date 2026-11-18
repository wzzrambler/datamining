from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


Text = Annotated[str, Field(min_length=1, max_length=4000)]
Code = Literal['JZG', 'GZCL', 'ZHCL']
Limit = Annotated[int, Field(ge=1, le=20)]
DayNumber = Annotated[int, Field(ge=1, le=30)]


class RouteSearch(Model):
    scenic_codes: list[Code] = Field(default_factory=list, max_length=3)
    days: DayNumber | None = None
    keywords: list[str] = Field(default_factory=list, max_length=12)
    must_visit_poi_ids: list[str] = Field(default_factory=list, max_length=20)
    avoid_poi_ids: list[str] = Field(default_factory=list, max_length=20)
    audience: str | None = None
    limit: Limit = 5


class RouteDetails(Model):
    route_id: str
    day: DayNumber | None = None
    step_ids: list[str] = Field(default_factory=list, max_length=40)
    topics: list[str] = Field(default_factory=list, max_length=10)
    cursor: int = Field(0, ge=0)


class PoiSearch(Model):
    scenic_code: Code | None = None
    keywords: list[str] = Field(default_factory=list, max_length=12)
    themes: list[str] = Field(default_factory=list, max_length=10)
    limit: Limit = 8


class PoiDetails(Model):
    poi_ids: list[str] = Field(min_length=1, max_length=8)
    topics: list[str] = Field(default_factory=list, max_length=10)
    route_id: str | None = None
    step_id: str | None = None


class Transport(Model):
    from_poi_id: str
    to_poi_id: str
    preferred_modes: list[str] = Field(default_factory=list, max_length=8)


class Sources(Model):
    ref_id: str | None = None
    entity_id: str | None = None
    route_id: str | None = None
    step_id: str | None = None
    cursor: int = Field(0, ge=0)

    @model_validator(mode='after')
    def require_target(self):
        if not any((self.ref_id, self.entity_id, self.route_id, self.step_id)):
            raise ValueError('请指定来源、实体、路线或步骤')
        return self


class AskUser(Model):
    question: Text
    fields: list[str] = Field(min_length=1, max_length=5)
    options: list[str] = Field(default_factory=list, max_length=8)


class Evidence(Model):
    kind: Literal['node', 'theme', 'document']
    id: str


class Item(Model):
    type: Literal['poi_visit', 'transport_leg', 'note']
    poi_id: str | None = None
    leg_id: str | None = None
    route_id: str | None = None
    step_id: str | None = None
    recommendation_reason: Text | None = None
    fields: dict = Field(default_factory=dict)
    evidence_refs: list[Evidence] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def check_item(self):
        if (self.type == 'poi_visit') != bool(self.poi_id):
            raise ValueError('景点到访必须且只能在 poi_visit 中填写 poi_id')
        if self.type == 'poi_visit' and not self.recommendation_reason:
            raise ValueError('景点到访须填写推荐理由')
        if (self.type == 'transport_leg') != bool(self.leg_id):
            raise ValueError('交通段必须且只能在 transport_leg 中填写 leg_id')
        if self.step_id and not self.route_id:
            raise ValueError('步骤须带有路线 ID')
        if self.type == 'note' and not isinstance(self.fields.get('text'), str):
            raise ValueError('备注须填写 fields.text')
        return self


class Day(Model):
    label: str | None = None
    date: str | None = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    schedule_basis: Literal['source', 'suggested', 'unknown'] = 'suggested'
    items: list[Item] = Field(max_length=80)


class SubmitItinerary(Model):
    title: Text
    days: list[Day] = Field(min_length=1, max_length=30)
    status: Literal['ready', 'partial'] = 'ready'
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    unresolved_items: list[dict] = Field(default_factory=list, max_length=30)


class ViewItinerary(Model):
    itinerary_id: UUID
    version: int | None = Field(None, ge=1)
    day_id: UUID | None = None
    type: Literal['poi_visit', 'transport_leg', 'note'] | None = None
    route_id: str | None = None
    entity_id: str | None = None


class Target(Model):
    item_id: UUID | None = None
    day_id: UUID | None = None
    route_id: str | None = None
    entity_id: str | None = None

    @model_validator(mode='after')
    def check_target(self):
        if not self.item_id and not (self.route_id and self.entity_id):
            raise ValueError('指定 item_id 或 route_id 与 entity_id')
        return self


class Anchor(Model):
    before_item_id: UUID | None = None
    after_item_id: UUID | None = None
    day_start: bool = False

    @model_validator(mode='after')
    def check_anchor(self):
        if sum(map(bool, (self.before_item_id, self.after_item_id, self.day_start))) != 1:
            raise ValueError('指定一个插入位置')
        return self


class UpdateFields(Model):
    op: Literal['update_fields']
    target: Target
    fields: dict = Field(min_length=1)


class Delete(Model):
    op: Literal['delete']
    target: Target


class Insert(Model):
    op: Literal['insert']
    day_id: UUID
    anchor: Anchor
    item: Item


Operation = Annotated[UpdateFields | Delete | Insert, Field(discriminator='op')]


class Edit(Model):
    base_version: int = Field(ge=1)
    operations: list[Operation] = Field(min_length=1, max_length=30)


class UpdateItinerary(Edit):
    itinerary_id: UUID


class ManualEdit(Edit):
    client_edit_id: UUID


class NewConversation(Model):
    title: str | None = Field(None, max_length=120)


class TargetRef(Model):
    type: Literal['route', 'entity', 'item']
    id: str = Field(min_length=1, max_length=200)


class NewTurn(Model):
    client_message_id: UUID
    text: Text
    reply_to: UUID | None = None
    target_ref: TargetRef | None = None

/* KeyFlow — «плата»-линии (CircuitBoard) поверх litegraph.
 * Адаптация алгоритма из ComfyUI-плагина quick-connections (docs/CircuitBoardLines.js):
 * связи рисуются ортогонально, по сетке, с обходом нод и скруглёнными углами.
 */
/* global LiteGraph, LGraphCanvas */
"use strict";

const EPSILON = 1e-6;

function clipT(num, denom, c) {
    if (Math.abs(denom) < EPSILON) return num < 0;
    const t = num / denom;
    if (denom > 0) {
        if (t > c[1]) return 0;
        if (t > c[0]) c[0] = t;
    } else {
        if (t < c[0]) return 0;
        if (t < c[1]) c[1] = t;
    }
    return 1;
}

/* Лианг-Барски: пересекает ли отрезок a-b прямоугольник box. */
function liangBarsky(a, b, box, da, db) {
    const x1 = a[0], y1 = a[1], x2 = b[0], y2 = b[1];
    const dx = x2 - x1, dy = y2 - y1;
    if (da === undefined || db === undefined) { da = a; db = b; }
    else { da[0] = a[0]; da[1] = a[1]; db[0] = b[0]; db[1] = b[1]; }
    if (Math.abs(dx) < EPSILON && Math.abs(dy) < EPSILON &&
        x1 >= box[0] && x1 <= box[2] && y1 >= box[1] && y1 <= box[3]) return 1;
    const c = [0, 1];
    if (clipT(box[0] - x1, dx, c) && clipT(x1 - box[2], -dx, c) &&
        clipT(box[1] - y1, dy, c) && clipT(y1 - box[3], -dy, c)) {
        if (c[1] < 1) { db[0] = x1 + c[1] * dx; db[1] = x1 + c[1] * dy; }
        if (c[0] > 0) { da[0] += c[0] * dx; da[1] += c[0] * dy; }
        return 1;
    }
    return 0;
}

class MapLinks {
    constructor(canvas) {
        this.canvas = canvas;
        this.nodesByRight = [];
        this.nodesById = [];
        this.paths = [];
        this.lineSpace = Math.floor(LiteGraph.NODE_SLOT_HEIGHT / 2);
        this.maxDirectLineDistance = Number.MAX_SAFE_INTEGER;
    }

    isInsideNode(xy) {
        for (let i = 0; i < this.nodesByRight.length; ++i) {
            if (this.nodesByRight[i].node.isPointInside(xy[0], xy[1])) return this.nodesByRight[i].node;
        }
        return null;
    }

    findClippedNode(outputXY, inputXY) {
        let closestDistance = Number.MAX_SAFE_INTEGER;
        let closest = null;
        for (let i = 0; i < this.nodesByRight.length; ++i) {
            const nodeI = this.nodesByRight[i];
            const clipA = [-1, -1], clipB = [-1, -1];
            const clipped = liangBarsky(outputXY, inputXY, nodeI.area, clipA, clipB);
            if (clipped === 1) {
                const centerX = nodeI.area[0] + (nodeI.area[2] - nodeI.area[0]) / 2;
                const centerY = nodeI.area[1] + (nodeI.area[3] - nodeI.area[1]) / 2;
                const dist = Math.hypot(centerX - outputXY[0], centerY - outputXY[1]);
                if (dist < closestDistance) {
                    closest = { start: clipA, end: clipB, node: nodeI };
                    closestDistance = dist;
                }
            }
        }
        return { clipped: closest, closestDistance };
    }

    testPath(path) {
        for (let p = 0; p < path.length - 1; ++p) {
            const { clipped } = this.findClippedNode(path[p], path[p + 1]);
            if (clipped) return clipped;
        }
        return null;
    }

    mapFinalLink(outputXY, inputXY) {
        const { clipped } = this.findClippedNode(outputXY, inputXY);
        if (!clipped) {
            const dist = Math.hypot(outputXY[0] - inputXY[0], outputXY[1] - inputXY[1]);
            if (dist < this.maxDirectLineDistance) return { path: [outputXY, inputXY] };
        }
        const horzDistance = inputXY[0] - outputXY[0];
        const vertDistance = inputXY[1] - outputXY[1];
        const horzAbs = Math.abs(horzDistance), vertAbs = Math.abs(vertDistance);
        if (horzAbs > vertAbs) {
            const goingLeft = inputXY[0] < outputXY[0];
            const pathStraight45 = [
                [outputXY[0], outputXY[1]],
                [inputXY[0] - (goingLeft ? -vertAbs : vertAbs), outputXY[1]],
                [inputXY[0], inputXY[1]],
            ];
            if (!this.testPath(pathStraight45)) return { path: pathStraight45 };
            const path45Straight = [
                [outputXY[0], outputXY[1]],
                [outputXY[0] + (goingLeft ? -vertAbs : vertAbs), inputXY[1]],
                [inputXY[0], inputXY[1]],
            ];
            if (!this.testPath(path45Straight)) return { path: path45Straight };
        } else {
            const goingUp = inputXY[1] < outputXY[1];
            const pathStraight45 = [
                [outputXY[0], outputXY[1]],
                [outputXY[0], inputXY[1] + (goingUp ? horzAbs : -horzAbs)],
                [inputXY[0], inputXY[1]],
            ];
            if (!this.testPath(pathStraight45)) return { path: pathStraight45 };
            const path45Straight = [
                [outputXY[0], outputXY[1]],
                [inputXY[0], outputXY[1] - (goingUp ? horzAbs : -horzAbs)],
                [inputXY[0], inputXY[1]],
            ];
            if (!this.testPath(path45Straight)) return { path: path45Straight };
        }
        const path90Straight = [
            [outputXY[0], outputXY[1]],
            [outputXY[0], inputXY[1]],
            [inputXY[0], inputXY[1]],
        ];
        const clippedVert = this.testPath(path90Straight);
        if (!clippedVert) return { path: path90Straight };
        const pathStraight90 = [
            [outputXY[0], outputXY[1]],
            [inputXY[0], outputXY[1]],
            [inputXY[0], inputXY[1]],
        ];
        const clippedHorz = this.testPath(pathStraight90);
        if (!clippedHorz) return { path: pathStraight90 };
        return { clippedHorz, clippedVert };
    }

    mapLink(outputXY, inputXY, targetNodeInfo, isBlocked, lastDirection) {
        const { clippedHorz, clippedVert, path } = this.mapFinalLink(outputXY, inputXY);
        if (path) return path;

        const horzDistance = inputXY[0] - outputXY[0];
        const vertDistance = inputXY[1] - outputXY[1];
        const horzAbs = Math.abs(horzDistance), vertAbs = Math.abs(vertDistance);

        let blockedNodeId, linesArea, pathAvoidNode, lastPathLocation, thisDirection = null;

        if (horzAbs > vertAbs) {
            blockedNodeId = clippedHorz.node.node.id;
            linesArea = clippedHorz.node.linesArea;
            const horzEdge = horzDistance <= 0 ? linesArea[2] : linesArea[0] - 1;
            pathAvoidNode = [[outputXY[0], outputXY[1]], [horzEdge, outputXY[1]]];
            if (horzDistance <= 0) linesArea[2] += this.lineSpace;
            else linesArea[0] -= this.lineSpace;

            const viaTop = Math.abs(inputXY[1] - linesArea[1]) + Math.abs(linesArea[1] - outputXY[1]);
            const viaBottom = Math.abs(inputXY[1] - linesArea[3]) + Math.abs(linesArea[3] - outputXY[1]);
            lastPathLocation = [horzEdge, viaTop <= viaBottom ? linesArea[1] : linesArea[3]];
            if (this.testPath([...pathAvoidNode, lastPathLocation])) {
                lastPathLocation = [horzEdge, viaTop > viaBottom ? linesArea[1] : linesArea[3]];
            }
            if (lastPathLocation[1] < outputXY[1]) {
                linesArea[1] -= this.lineSpace;
                lastPathLocation[1] -= 1;
            } else {
                linesArea[3] += this.lineSpace;
                lastPathLocation[1] += 1;
            }
            thisDirection = 'vert';
        } else {
            blockedNodeId = clippedVert.node.node.id;
            linesArea = clippedVert.node.linesArea;
            const vertEdge = vertDistance <= 0 ? linesArea[3] + 1 : linesArea[1] - 1;
            pathAvoidNode = [[outputXY[0], outputXY[1]], [outputXY[0], vertEdge]];
            if (vertDistance <= 0) linesArea[3] += this.lineSpace;
            else linesArea[1] -= this.lineSpace;

            const viaLeft = Math.abs(inputXY[0] - linesArea[0]) + Math.abs(linesArea[0] - outputXY[0]);
            const viaRight = Math.abs(inputXY[0] - linesArea[2]) + Math.abs(linesArea[2] - outputXY[0]);
            lastPathLocation = [viaLeft <= viaRight ? linesArea[0] - 1 : linesArea[2], vertEdge];
            if (this.testPath([...pathAvoidNode, lastPathLocation])) {
                lastPathLocation = [viaLeft > viaRight ? linesArea[0] : linesArea[2], vertEdge];
            }
            if (lastPathLocation[0] < outputXY[0]) {
                linesArea[0] -= this.lineSpace;
            } else {
                linesArea[2] += this.lineSpace;
            }
            thisDirection = 'horz';
        }

        if (isBlocked[blockedNodeId] > 3) return [outputXY, inputXY];
        if (isBlocked[blockedNodeId]) ++isBlocked[blockedNodeId];
        else isBlocked[blockedNodeId] = 1;

        const nextPath = this.mapLink(lastPathLocation, inputXY, targetNodeInfo, isBlocked, thisDirection);
        return [...pathAvoidNode, lastPathLocation, ...nextPath.slice(1)];
    }

    expandSourceNodeLinesArea(sourceNodeInfo, path) {
        if (path.length < 3) return false;
        if (path[1][0] === path[2][0]) sourceNodeInfo.linesArea[2] += this.lineSpace;
        return true;
    }

    expandTargetNodeLinesArea(targetNodeInfo, path) {
        if (path.length < 2) return false;
        const linesArea = targetNodeInfo.linesArea;
        const p2 = path.length - 2;
        if (path[p2 - 1][0] === path[p2][0]) linesArea[0] -= this.lineSpace;
        return true;
    }

    getNodeOnPos(xy) {
        for (let i = 0; i < this.nodesByRight.length; ++i) {
            const la = this.nodesByRight[i].linesArea;
            if (xy[0] >= la[0] && xy[1] >= la[1] && xy[0] < la[2] && xy[1] < la[3])
                return this.nodesByRight[i];
        }
        return null;
    }

    mapLinks(nodesByExecution) {
        if (!this.canvas.graph || !this.canvas.graph.links) return;
        this.links = [];
        this.nodesByRight = [];
        this.nodesById = {};
        this.nodesByRight = nodesByExecution.map((node) => {
            const barea = new Float32Array(4);
            node.getBounding(barea);
            const area = [barea[0], barea[1], barea[0] + barea[2], barea[1] + barea[3]];
            const linesArea = Array.from(area);
            linesArea[0] -= 5; linesArea[1] -= 1; linesArea[2] += 3; linesArea[3] += 3;
            const obj = { node, area, linesArea };
            this.nodesById[node.id] = obj;
            return obj;
        });

        for (const nodeI of this.nodesByRight) {
            const node = nodeI.node;
            if (!node.outputs) continue;
            for (let slot = 0; slot < node.outputs.length; ++slot) {
                const output = node.outputs[slot];
                if (!output.links || !output.links.length) continue;
                const linkPos = new Float32Array(2);
                const outputXYConnection = node.getConnectionPos(false, slot, linkPos);
                let outputXY = Array.from(outputXYConnection);
                output.links.forEach((linkId) => {
                    outputXY[0] = nodeI.linesArea[2];
                    const link = this.canvas.graph.links[linkId];
                    if (!link) return;
                    const targetNode = this.canvas.graph.getNodeById(link.target_id);
                    if (!targetNode) return;
                    const inputLinkPos = new Float32Array(2);
                    const inputXYConnection = targetNode.getConnectionPos(true, link.target_slot, inputLinkPos);
                    const inputXY = Array.from(inputXYConnection);
                    const nodeInfo = this.nodesById[targetNode.id];
                    inputXY[0] = nodeInfo.linesArea[0] - 1;

                    const inputBlockedByNode = this.getNodeOnPos(inputXY);
                    const outputBlockedByNode = this.getNodeOnPos(outputXY);

                    let path = null;
                    if (!inputBlockedByNode && !outputBlockedByNode) {
                        const pathFound = this.mapLink(outputXY, inputXY, nodeInfo, {}, null);
                        if (pathFound && pathFound.length > 2) {
                            path = [outputXYConnection, ...pathFound, inputXYConnection];
                            this.expandTargetNodeLinesArea(nodeInfo, path);
                        }
                    }
                    if (!path) {
                        path = [outputXYConnection, outputXY, inputXY, inputXYConnection];
                    }
                    this.expandSourceNodeLinesArea(nodeI, path);
                    this.paths.push({ path, node, targetNode, slot });
                    outputXY = [outputXY[0] + this.lineSpace, outputXY[1]];
                });
            }
        }
    }

    drawLinks(ctx) {
        if (!this.canvas.default_connection_color_byType || !this.canvas.default_connection_color) return;
        ctx.save();
        const currentNodeIds = this.canvas.selected_nodes || {};
        for (const pathI of this.paths) {
            const path = pathI.path;
            if (path.length <= 1) continue;
            ctx.beginPath();
            const connection = pathI.node.outputs[pathI.slot];
            const slotColor =
                this.canvas.default_connection_color_byType[connection.type] ||
                this.canvas.default_connection_color.input_on;
            ctx.strokeStyle = (currentNodeIds[pathI.node.id] || currentNodeIds[pathI.targetNode.id])
                ? "#ffffff" : slotColor;
            ctx.lineWidth = 3;
            const cornerRadius = this.lineSpace;
            let isPrevDotRound = false;
            for (let p = 0; p < path.length; ++p) {
                const pos = path[p];
                if (p === 0) ctx.moveTo(pos[0], pos[1]);
                const cornerPos = path[p + 1];
                const nextPos = path[p + 2];
                let drawn = false;
                if (nextPos) {
                    const xDiffBefore = cornerPos[0] - pos[0];
                    const yDiffBefore = cornerPos[1] - pos[1];
                    const xDiffAfter = nextPos[0] - cornerPos[0];
                    const yDiffAfter = nextPos[1] - cornerPos[1];
                    const isBeforeStraight = xDiffBefore === 0 || yDiffBefore === 0;
                    const isAfterStraight = xDiffAfter === 0 || yDiffAfter === 0;
                    if (isBeforeStraight || isAfterStraight) {
                        const beforePos = [cornerPos[0], cornerPos[1]];
                        const afterPos = [cornerPos[0], cornerPos[1]];
                        if (isBeforeStraight) {
                            beforePos[0] = cornerPos[0] - cornerRadius * Math.sign(xDiffBefore);
                            beforePos[1] = cornerPos[1] - cornerRadius * Math.sign(yDiffBefore);
                        }
                        if (isAfterStraight) {
                            afterPos[0] = cornerPos[0] + cornerRadius * Math.sign(xDiffAfter);
                            afterPos[1] = cornerPos[1] + cornerRadius * Math.sign(yDiffAfter);
                        }
                        if (isPrevDotRound &&
                            Math.abs(isPrevDotRound[0] - beforePos[0]) <= cornerRadius &&
                            Math.abs(isPrevDotRound[1] - beforePos[1]) <= cornerRadius) {
                            ctx.lineTo(cornerPos[0], cornerPos[1]);
                        } else {
                            ctx.lineTo(beforePos[0], beforePos[1]);
                            ctx.quadraticCurveTo(cornerPos[0], cornerPos[1], afterPos[0], afterPos[1]);
                        }
                        isPrevDotRound = beforePos;
                        drawn = true;
                    }
                }
                if (p > 0 && !drawn) {
                    if (!isPrevDotRound) ctx.lineTo(pos[0], pos[1]);
                    isPrevDotRound = false;
                }
            }
            ctx.stroke();
            ctx.closePath();
            // точки на обоих концах связи: видно, где линия берёт начало и куда приходит
            ctx.fillStyle = ctx.strokeStyle;
            for (const endPos of [path[0], path[path.length - 1]]) {
                ctx.beginPath();
                ctx.arc(endPos[0], endPos[1], 4, 0, Math.PI * 2);
                ctx.fill();
            }
        }
        ctx.restore();
    }
}

class CircuitBoard {
    constructor() {
        this.canvas = null;
        this.mapLinks = null;
        this.enabled = true;
        this.maxDirectLineDistance = Number.MAX_SAFE_INTEGER;
    }

    setEnabled(e) { this.enabled = !!e; }
    isShow() { return this.enabled; }

    recalcMapLinks() {
        if (!this.canvas || !this.canvas.graph) return;
        this.mapLinks = new MapLinks(this.canvas);
        this.mapLinks.maxDirectLineDistance = this.maxDirectLineDistance;
        // ВАЖНО: берём ВСЕ ноды, а не computeExecutionOrder() — топологическая
        // сортировка теряет ноды в циклах/с зависшими связями, и их линии
        // молча не рисовались (связь при этом существовала).
        const nodesByExecution = this.canvas.graph._nodes || [];
        this.mapLinks.mapLinks(nodesByExecution);
    }

    drawConnections(ctx) {
        if (!this.canvas || !this.canvas.graph) return false;
        try {
            this.recalcMapLinks();
            if (this.mapLinks) this.mapLinks.drawLinks(ctx);
        } catch (e) {
            // при любой проблеме просто рисуем стандартные линии
            if (this._origDrawConnections) this._origDrawConnections.call(this.canvas, ctx);
        }
        return true;
    }

    init() {
        if (this._patched) return;
        this._patched = true;
        const t = this;
        t._origDrawConnections = LGraphCanvas.prototype.drawConnections;
        LGraphCanvas.prototype.drawConnections = function (ctx) {
            if (t.hideLinks) return;   // KeyFlow: линии скрыты кнопкой на панели
            if (t.canvas && t.isShow()) return t.drawConnections(ctx);
            return t._origDrawConnections.apply(this, arguments);
        };
    }

    initOverrides(canvas) {
        this.canvas = canvas;
    }
}

window.CircuitBoard = CircuitBoard;

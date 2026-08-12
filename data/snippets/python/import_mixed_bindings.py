import pygame as pg
from collections import Counter

def draw(screen, color, rect):
    pg.draw.rect(screen, color, rect)
    return Counter([1, 2])

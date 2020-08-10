import warnings
import numpy as np
import math
import attr
import typing as tp

from colour import Color
from ..constants import *
from ..mobject.mobject import Mobject
from ..mobject.types.vectorized_mobject import VGroup
from ..mobject.types.vectorized_mobject import VMobject
from ..mobject.types.vectorized_mobject import DashedVMobject
from ..utils.config_ops import digest_config
from ..utils.iterables import adjacent_n_tuples
from ..utils.iterables import adjacent_pairs
from ..utils.simple_functions import fdiv
from ..utils.space_ops import angle_of_vector
from ..utils.space_ops import angle_between_vectors
from ..utils.space_ops import compass_directions
from ..utils.space_ops import line_intersection
from ..utils.space_ops import get_norm
from ..utils.space_ops import normalize
from ..utils.space_ops import rotate_vector
from ..utils.dataclasses import dclass


DEFAULT_DOT_RADIUS = 0.08
DEFAULT_SMALL_DOT_RADIUS = 0.04
DEFAULT_DASH_LENGTH = 0.05
DEFAULT_ARROW_TIP_LENGTH = 0.35


@dclass
class TipableVMobject(VMobject):
    """
    Meant for shared functionality between Arc and Line.
    Functionality can be classified broadly into these groups:

        * Adding, Creating, Modifying tips
            - add_tip calls create_tip, before pushing the new tip
                into the TipableVMobject's list of submobjects
            - stylistic and positional configuration

        * Checking for tips
            - Boolean checks for whether the TipableVMobject has a tip
                and a starting tip

        * Getters
            - Straightforward accessors, returning information pertaining
                to the TipableVMobject instance's tip(s), its length etc

    """

    tip_length: float = DEFAULT_ARROW_TIP_LENGTH
    normal_vector: np.ndarray = OUT
    tip_style: dict = {"fill_opacity": 1, "stroke_width": 0}

    def __attrs_post_init__(self):
        VMobject.__attrs_post_init__(self)

    # Adding, Creating, Modifying tips

    def add_tip(self, tip_length=None, at_start=False):
        """
        Adds a tip to the TipableVMobject instance, recognising
        that the endpoints might need to be switched if it's
        a 'starting tip' or not.
        """
        tip = self.create_tip(tip_length, at_start)
        self.reset_endpoints_based_on_tip(tip, at_start)
        self.asign_tip_attr(tip, at_start)
        self.add(tip)
        return self

    def create_tip(self, tip_length=None, at_start=False):
        """
        Stylises the tip, positions it spacially, and returns
        the newly instantiated tip to the caller.
        """
        tip = self.get_unpositioned_tip(tip_length)
        self.position_tip(tip, at_start)
        return tip

    def get_unpositioned_tip(self, tip_length=None):
        """
        Returns a tip that has been stylistically configured,
        but has not yet been given a position in space.
        """
        if tip_length is None:
            tip_length = self.get_default_tip_length()
        color = self.get_color()
        style = {"fill_color": color, "stroke_color": color}
        style.update(self.tip_style)
        tip = ArrowTip(length=tip_length, **style)
        return tip

    def position_tip(self, tip, at_start=False):
        # Last two control points, defining both
        # the end, and the tangency direction
        if at_start:
            anchor = self.get_start()
            handle = self.get_first_handle()
        else:
            handle = self.get_last_handle()
            anchor = self.get_end()
        tip.rotate(angle_of_vector(handle - anchor) - PI - tip.get_angle())
        tip.shift(anchor - tip.get_tip_point())
        return tip

    def reset_endpoints_based_on_tip(self, tip, at_start):
        if self.get_length() == 0:
            # Zero length, put_start_and_end_on wouldn't
            # work
            return self

        if at_start:
            self.put_start_and_end_on(tip.get_base(), self.get_end())
        else:
            self.put_start_and_end_on(
                self.get_start(), tip.get_base(),
            )
        return self

    def asign_tip_attr(self, tip, at_start):
        if at_start:
            self.start_tip = tip
        else:
            self.tip = tip
        return self

    # Checking for tips

    def has_tip(self):
        return hasattr(self, "tip") and self.tip in self

    def has_start_tip(self):
        return hasattr(self, "start_tip") and self.start_tip in self

    # Getters

    def pop_tips(self):
        start, end = self.get_start_and_end()
        result = VGroup()
        if self.has_tip():
            result.add(self.tip)
            self.remove(self.tip)
        if self.has_start_tip():
            result.add(self.start_tip)
            self.remove(self.start_tip)
        self.put_start_and_end_on(start, end)
        return result

    def get_tips(self):
        """
        Returns a VGroup (collection of VMobjects) containing
        the TipableVMObject instance's tips.
        """
        result = VGroup()
        if hasattr(self, "tip"):
            result.add(self.tip)
        if hasattr(self, "start_tip"):
            result.add(self.start_tip)
        return result

    def get_tip(self):
        """Returns the TipableVMobject instance's (first) tip,
        otherwise throws an exception."""
        tips = self.get_tips()
        if len(tips) == 0:
            raise Exception("tip not found")
        else:
            return tips[0]

    def get_default_tip_length(self):
        return self.tip_length

    def get_first_handle(self):
        return self.points[1]

    def get_last_handle(self):
        return self.points[-2]

    def get_end(self):
        if self.has_tip():
            return self.tip.get_start()
        else:
            return VMobject.get_end(self)

    def get_start(self):
        if self.has_start_tip():
            return self.start_tip.get_start()
        else:
            return VMobject.get_start(self)

    def get_length(self):
        start, end = self.get_start_and_end()
        return get_norm(start - end)


@dclass
class Arc(TipableVMobject):
    radius: float = 1.0
    num_components: int = 9
    anchors_span_full_range: bool = True
    arc_center: np.ndarray = ORIGIN
    start_angle: float = 0
    angle: float = TAU / 4
    _failed_to_get_center: bool = attr.attrib(init=False, default=False)

    def __attrs_post_init__(self):
        TipableVMobject.__attrs_post_init__(self)

    def generate_points(self):
        self.set_pre_positioned_points()
        if self.radius is None:
            self.radius = 1.0
        self.scale(self.radius, about_point=ORIGIN)
        self.shift(self.arc_center)

    def set_pre_positioned_points(self):
        anchors = np.array(
            [
                np.cos(a) * RIGHT + np.sin(a) * UP
                for a in np.linspace(
                    self.start_angle,
                    self.start_angle + self.angle,
                    self.num_components,
                )
            ]
        )
        # Figure out which control points will give the
        # Appropriate tangent lines to the circle
        d_theta = self.angle / (self.num_components - 1.0)
        tangent_vectors = np.zeros(anchors.shape)
        # Rotate all 90 degress, via (x, y) -> (-y, x)
        tangent_vectors[:, 1] = anchors[:, 0]
        tangent_vectors[:, 0] = -anchors[:, 1]
        # Use tangent vectors to deduce anchors
        handles1 = anchors[:-1] + (d_theta / 3) * tangent_vectors[:-1]
        handles2 = anchors[1:] - (d_theta / 3) * tangent_vectors[1:]
        self.set_anchors_and_handles(
            anchors[:-1], handles1, handles2, anchors[1:],
        )

    def get_arc_center(self, warning=True):
        """
        Looks at the normals to the first two
        anchors, and finds their intersection points
        """
        # First two anchors and handles
        a1, h1, h2, a2 = self.points[:4]
        # Tangent vectors
        t1 = h1 - a1
        t2 = h2 - a2
        # Normals
        n1 = rotate_vector(t1, TAU / 4)
        n2 = rotate_vector(t2, TAU / 4)
        try:
            return line_intersection(line1=(a1, a1 + n1), line2=(a2, a2 + n2),)
        except Exception:
            if warning:
                warnings.warn("Can't find Arc center, using ORIGIN instead")
            self._failed_to_get_center = True
            return np.array(ORIGIN)

    def move_arc_center_to(self, point):
        self.shift(point - self.get_arc_center())
        return self

    def stop_angle(self):
        return angle_of_vector(self.points[-1] - self.get_arc_center()) % TAU


@dclass
class ArcBetweenPoints(Arc):
    """
    Inherits from Arc and additionally takes 2 points between which the arc is spanned.
    """

    start: tp.Any = None
    end: tp.Any = None
    angle: float = TAU / 4
    radius: tp.Optional[float] = None

    # TODO perhaps this should be a class method of Arc
    def __attrs_post_init__(self):
        if self.radius is not None:
            if self.radius < 0:
                sign = -2
                self.radius *= -1
            else:
                sign = 2
            halfdist = np.linalg.norm(np.array(self.start) - np.array(self.end)) / 2
            if self.radius < halfdist:
                raise ValueError(
                    """ArcBetweenPoints called with a radius that is
                            smaller than half the distance between the points."""
                )
            arc_height = self.radius - math.sqrt(self.radius ** 2 - halfdist ** 2)
            self.angle = math.acos((self.radius - arc_height) / self.radius) * sign
        Arc.__attrs_post_init__(self)
        if self.angle == 0:
            self.set_points_as_corners([LEFT, RIGHT])
        self.put_start_and_end_on(self.start, self.end)
        if self.radius is None:
            center = self.get_arc_center(warning=False)
            if not self._failed_to_get_center:
                self.radius = np.linalg.norm(np.array(self.start) - np.array(center))
            else:
                self.radius = math.inf


@dclass
class CurvedArrow(ArcBetweenPoints):
    start_point: tp.Any = None
    end_point: tp.Any = None

    def __attrs_post_init__(self):
        self.start = self.start_point
        self.end = self.end_point
        ArcBetweenPoints.__attrs_post_init__(self)
        self.add_tip()


@dclass
class CurvedDoubleArrow(CurvedArrow):
    def __attrs_post_init__(self):
        CurvedArrow.__attrs_post_init__(self)
        self.add_tip(at_start=True)


@dclass
class Circle(Arc):
    color: tp.Union[str, Color] = RED
    close_new_points: bool = True
    anchors_span_full_range: bool = False

    def __attrs_post_init__(self):
        self.start_angle = 0
        self.angle = TAU
        Arc.__attrs_post_init__(self)

    def surround(self, mobject, dim_to_match=0, stretch=False, buffer_factor=1.2):
        # Ignores dim_to_match and stretch; result will always be a circle
        # TODO: Perhaps create an ellipse class to handle singele-dimension stretching

        # Something goes wrong here when surrounding lines?
        # TODO: Figure out and fix
        self.replace(mobject, dim_to_match, stretch)

        self.set_width(np.sqrt(mobject.get_width() ** 2 + mobject.get_height() ** 2))
        self.scale(buffer_factor)

    def point_at_angle(self, angle):
        start_angle = angle_of_vector(self.points[0] - self.get_center())
        return self.point_from_proportion((angle - start_angle) / TAU)


@dclass
class Dot(Circle):
    radius: float = DEFAULT_DOT_RADIUS
    stroke_width: float = 0
    fill_opacity: float = 1.0
    color: tp.Union[str, Color] = WHITE
    point: tp.Any = ORIGIN

    def __attrs_post_init__(self):
        self.arc_center = self.point
        Circle.__attrs_post_init__(self)


@dclass
class SmallDot(Dot):
    radius: float = DEFAULT_SMALL_DOT_RADIUS

    def __attrs_post_init__(self):
        Dot.__attrs_post_init__(self)


@dclass
class Ellipse(Circle):
    width: float = 2.0
    height: float = 1.0

    def __attrs_post_init__(self):
        Circle.__attrs_post_init__(self)
        self.set_width(self.width, stretch=True)
        self.set_height(self.height, stretch=True)


@dclass
class AnnularSector(Arc):
    inner_radius: float = 1.0
    outer_radius: float = 2.0
    angle: float = TAU / 4
    start_angle: float = 0
    fill_opacity: float = 1.0
    stroke_width: float = 0.0
    color: tp.Union[str, Color] = WHITE

    def __attrs_post_init__(self):
        Arc.__attrs_post_init__(self)

    def generate_points(self):
        inner_arc, outer_arc = [
            Arc(
                start_angle=self.start_angle,
                angle=self.angle,
                radius=radius,
                arc_center=self.arc_center,
            )
            for radius in (self.inner_radius, self.outer_radius)
        ]
        outer_arc.reverse_points()
        self.append_points(inner_arc.points)
        self.add_line_to(outer_arc.points[0])
        self.append_points(outer_arc.points)
        self.add_line_to(inner_arc.points[0])


@dclass
class Sector(AnnularSector):
    outer_radius: float = 1.0
    inner_radius: float = 0.0

    def __attrs_post_init__(self):
        AnnularSector.__attrs_post_init__(self)


@dclass
class Annulus(Circle):
    inner_radius: float = 1.0
    outer_radius: float = 2.0
    fill_opacity: float = 1.0
    stroke_width: float = 0.0
    color: tp.Union[str, Color] = WHITE
    mark_paths_closed: bool = False

    def __attrs_post_init__(self):
        Circle.__attrs_post_init__(self)

    def generate_points(self):
        self.radius = self.outer_radius
        outer_circle = Circle(radius=self.outer_radius)
        inner_circle = Circle(radius=self.inner_radius)
        inner_circle.reverse_points()
        self.append_points(outer_circle.points)
        self.append_points(inner_circle.points)
        self.shift(self.arc_center)


@dclass
class Line(TipableVMobject):
    buff: float = 0
    path_arc: tp.Any = None
    start: np.ndarray = LEFT
    end: np.ndarray = RIGHT

    # TODO this should probably be a class method instead
    def __attrs_post_init__(self):
        self.set_start_and_end_attrs(self.start, self.end)
        VMobject.__attrs_post_init__(self)

    def generate_points(self):
        if self.path_arc:
            arc = ArcBetweenPoints(self.start, self.end, angle=self.path_arc)
            self.set_points(arc.points)
        else:
            self.set_points_as_corners([self.start, self.end])
        self.account_for_buff()

    def set_path_arc(self, new_value):
        self.path_arc = new_value
        self.generate_points()

    def account_for_buff(self):
        if self.buff == 0:
            return
        #
        if self.path_arc == 0:
            length = self.get_length()
        else:
            length = self.get_arc_length()
        #
        if length < 2 * self.buff:
            return
        buff_proportion = self.buff / length
        self.pointwise_become_partial(self, buff_proportion, 1 - buff_proportion)
        return self

    def set_start_and_end_attrs(self, start, end):
        # If either start or end are Mobjects, this
        # gives their centers
        rough_start = self.pointify(start)
        rough_end = self.pointify(end)
        vect = normalize(rough_end - rough_start)
        # Now that we know the direction between them,
        # we can the appropriate boundary point from
        # start and end, if they're mobjects
        self.start = self.pointify(start, vect)
        self.end = self.pointify(end, -vect)

    def pointify(self, mob_or_point, direction=None):
        if isinstance(mob_or_point, Mobject):
            mob = mob_or_point
            if direction is None:
                return mob.get_center()
            else:
                return mob.get_boundary_point(direction)
        return np.array(mob_or_point)

    def put_start_and_end_on(self, start, end):
        curr_start, curr_end = self.get_start_and_end()
        if np.all(curr_start == curr_end):
            # TODO, any problems with resetting
            # these attrs?
            self.start = start
            self.end = end
            self.generate_points()
        return super().put_start_and_end_on(start, end)

    def get_vector(self):
        return self.get_end() - self.get_start()

    def get_unit_vector(self):
        return normalize(self.get_vector())

    def get_angle(self):
        return angle_of_vector(self.get_vector())

    def get_slope(self):
        return np.tan(self.get_angle())

    def set_angle(self, angle):
        self.rotate(
            angle - self.get_angle(), about_point=self.get_start(),
        )

    def set_length(self, length):
        self.scale(length / self.get_length())

    def set_opacity(self, opacity, family=True):
        # Overwrite default, which would set
        # the fill opacity
        self.set_stroke(opacity=opacity)
        if family:
            for sm in self.submobjects:
                sm.set_opacity(opacity, family)
        return self


@dclass
class DashedLine(Line):
    dash_length: float = DEFAULT_DASH_LENGTH
    dash_spacing: tp.Optional[float] = None
    positive_space_ratio: float = 0.5

    def __attrs_post_init__(self):
        Line.__attrs_post_init__(self)
        ps_ratio = self.positive_space_ratio
        num_dashes = self.calculate_num_dashes(ps_ratio)
        dashes = DashedVMobject(
            self, num_dashes=num_dashes, positive_space_ratio=ps_ratio
        )
        self.clear_points()
        self.add(*dashes)

    def calculate_num_dashes(self, positive_space_ratio):
        try:
            full_length = self.dash_length / positive_space_ratio
            return int(np.ceil(self.get_length() / full_length))
        except ZeroDivisionError:
            return 1

    def calculate_positive_space_ratio(self):
        return fdiv(self.dash_length, self.dash_length + self.dash_spacing,)

    def get_start(self):
        if len(self.submobjects) > 0:
            return self.submobjects[0].get_start()
        else:
            return Line.get_start(self)

    def get_end(self):
        if len(self.submobjects) > 0:
            return self.submobjects[-1].get_end()
        else:
            return Line.get_end(self)

    def get_first_handle(self):
        return self.submobjects[0].points[1]

    def get_last_handle(self):
        return self.submobjects[-1].points[-2]


@dclass
class TangentLine(Line):
    length: float = 1.0
    d_alpha: float = 1e-6
    CONFIG = {"length": 1, "d_alpha": 1e-6}
    vmob: tp.Any = None
    alpha: tp.Any = None

    def __attrs_post_init__(self):
        da = self.d_alpha
        a1 = np.clip(self.alpha - da, 0, 1)
        a2 = np.clip(self.alpha + da, 0, 1)
        self.left = self.vmob.point_from_proportion(a1)
        self.right = self.vmob.point_from_proportion(a2)
        Line.__attrs_post_init__(self)
        self.scale(self.length / self.get_length())


@dclass
class Elbow(VMobject):
    width: float = 0.2
    angle: float = 0

    def __attrs_post_init__(self):
        VMobject.__attrs_post_init__(self)
        self.set_points_as_corners([UP, UP + RIGHT, RIGHT])
        self.set_width(self.width, about_point=ORIGIN)
        self.rotate(self.angle, about_point=ORIGIN)


@dclass
class Arrow(Line):
    stroke_width: float = 6.0
    buff: float = MED_SMALL_BUFF
    max_tip_length_to_length_ratio: float = 0.25
    max_stroke_width_to_length_ratio: float = 5
    preserve_tip_size_when_scaling: bool = True

    def __attrs_post_init__(self):
        Line.__attrs_post_init__(self)
        self.initial_stroke_width = self.stroke_width
        self.add_tip()
        self.set_stroke_width_from_length()

    def scale(self, factor, **kwargs):
        if self.get_length() == 0:
            return self

        has_tip = self.has_tip()
        has_start_tip = self.has_start_tip()
        if has_tip or has_start_tip:
            old_tips = self.pop_tips()

        VMobject.scale(self, factor, **kwargs)
        self.set_stroke_width_from_length()

        # So horribly confusing, must redo
        if has_tip:
            self.add_tip()
            old_tips[0].points[:, :] = self.tip.points
            self.remove(self.tip)
            self.tip = old_tips[0]
            self.add(self.tip)
        if has_start_tip:
            self.add_tip(at_start=True)
            old_tips[1].points[:, :] = self.start_tip.points
            self.remove(self.start_tip)
            self.start_tip = old_tips[1]
            self.add(self.start_tip)
        return self

    def get_normal_vector(self):
        p0, p1, p2 = self.tip.get_start_anchors()[:3]
        return normalize(np.cross(p2 - p1, p1 - p0))

    def reset_normal_vector(self):
        self.normal_vector = self.get_normal_vector()
        return self

    def get_default_tip_length(self):
        max_ratio = self.max_tip_length_to_length_ratio
        return min(self.tip_length, max_ratio * self.get_length(),)

    def set_stroke_width_from_length(self):
        max_ratio = self.max_stroke_width_to_length_ratio
        self.set_stroke(
            width=min(self.initial_stroke_width, max_ratio * self.get_length(),),
            family=False,
        )
        return self

    # TODO, should this be the default for everything?
    def copy(self):
        return self.deepcopy()


@dclass
class Vector(Arrow):
    buff: float = 0.0
    direction: np.ndarray = RIGHT

    def __attrs_post_init__(self):
        if len(self.direction) == 2:
            self.direction = np.append(np.array(self.direction), 0)
        self.left = ORIGIN
        self.right = self.direction
        Arrow.__attrs_post_init__(self)


@dclass
class DoubleArrow(Arrow):
    def __attrs_post_init__(self):
        Arrow.__attrs_post_init__(self)
        self.add_tip(at_start=True)


@dclass
class CubicBezier(VMobject):
    def __attrs_post_init__(self):
        VMobject.__attrs_post_init__(self)
        self.set_points(self.points)


@dclass
class Polygon(VMobject):
    vertices: tp.List = []  # TODO add validator
    color: tp.Union[str, Color] = BLUE

    def __attrs_post_init__(self):
        VMobject.__attrs_post_init__(self)
        self.set_points_as_corners([*self.vertices, self.vertices[0]])

    def get_vertices(self):
        return self.get_start_anchors()

    def round_corners(self, radius=0.5):
        vertices = self.get_vertices()
        arcs = []
        for v1, v2, v3 in adjacent_n_tuples(vertices, 3):
            vect1 = v2 - v1
            vect2 = v3 - v2
            unit_vect1 = normalize(vect1)
            unit_vect2 = normalize(vect2)
            angle = angle_between_vectors(vect1, vect2)
            # Negative radius gives concave curves
            angle *= np.sign(radius)
            # Distance between vertex and start of the arc
            cut_off_length = radius * np.tan(angle / 2)
            # Determines counterclockwise vs. clockwise
            sign = np.sign(np.cross(vect1, vect2)[2])
            arc = ArcBetweenPoints(
                v2 - unit_vect1 * cut_off_length,
                v2 + unit_vect2 * cut_off_length,
                angle=sign * angle,
            )
            arcs.append(arc)

        self.clear_points()
        # To ensure that we loop through starting with last
        arcs = [arcs[-1], *arcs[:-1]]
        for arc1, arc2 in adjacent_pairs(arcs):
            self.append_points(arc1.points)
            line = Line(arc1.get_end(), arc2.get_start())
            # Make sure anchors are evenly distributed
            len_ratio = line.get_length() / arc1.get_arc_length()
            line.insert_n_curves(int(arc1.get_num_curves() * len_ratio))
            self.append_points(line.get_points())
        return self


@dclass
class RegularPolygon(Polygon):
    start_angle: float = None
    n: int = 6

    def __attrs_post_init__(self):
        digest_config(self, {}, locals())
        if self.start_angle is None:
            if self.n % 2 == 0:
                self.start_angle = 0
            else:
                self.start_angle = 90 * DEGREES
        start_vect = rotate_vector(RIGHT, self.start_angle)
        self.vertices = compass_directions(self.n, start_vect)
        Polygon.__attrs_post_init__(self)


@dclass
class Triangle(RegularPolygon):
    def __attrs_post_init__(self):
        self.n = 3
        RegularPolygon.__attrs_post_init__(self)


@dclass
class ArrowTip(Triangle):
    fill_opacity: float = 1
    stroke_width: float = 0
    length: float = DEFAULT_ARROW_TIP_LENGTH
    start_angle: float = PI

    def __attrs_post_init__(self):
        Triangle.__attrs_post_init__(self)
        self.set_width(self.length)
        self.set_height(self.length, stretch=True)

    def get_base(self):
        return self.point_from_proportion(0.5)

    def get_tip_point(self):
        return self.points[0]

    def get_vector(self):
        return self.get_tip_point() - self.get_base()

    def get_angle(self):
        return angle_of_vector(self.get_vector())

    def get_length(self):
        return get_norm(self.get_vector())


@dclass
class Rectangle(Polygon):
    color: tp.Union[str, Color] = WHITE
    height: float = 2.0
    width: float = 4.0
    mark_paths_closed: bool = True
    close_new_points: bool = True
    vertices: np.ndarray = [UL, UR, DR, DL]

    def __attrs_post_init__(self):
        Polygon.__attrs_post_init__(self)
        self.set_width(self.width, stretch=True)
        self.set_height(self.height, stretch=True)


@dclass
class Square(Rectangle):
    side_length: float = 2.0

    def __attrs_post_init__(self):
        self.height = self.side_length
        self.width = self.side_length
        Rectangle.__attrs_post_init__(self)


@dclass
class RoundedRectangle(Rectangle):
    corner_radius: float = 0.5

    def __attrs_post_init__(self):
        Rectangle.__attrs_post_init__(self)
        self.round_corners(self.corner_radius)
